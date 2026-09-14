# [Bug][DSpark] SPS cost-table profiling is unusable on multi-node TP4: the profiler's required `SGLANG_SIMULATE_ACC_LEN=1.0` + CUDA graphs installs an unguarded verify-epilogue capture hook

**Status:** drafted for upstream filing — not yet posted (owner review pending)
**Filed by:** BertholomusAI (recipe/runtime operator for this deployment)

## Environment

- 4 × NVIDIA GB10 (SM 12.1) DGX Spark-class nodes, single 4-node group, `nnodes=4`, `tp_size=4`, no expert parallel
- SGLang build pinned in our runtime image (`sgl-workspace/sglang`, DSpark speculative components present under `sglang/srt/speculative/dspark_components/`)
- DSpark speculative decoding: `DSPARK_BLOCK_SIZE=3` (⇒ `verify_num_draft_tokens=4`), confidence head **not** built (ragged-verify mode `static`)
- CUDA graphs enabled (decode graph, `max_bs=8`), `attention_backend=dsv4`, fp8 KV, `moe_runner_backend=flashinfer_mxfp4`
- Verified on the live deployment 2026-09-14; source citations read from the running image on that date

## Summary

`python -m sglang.benchmark.dspark_sps_profiler` cannot be run against this deployment, and there is **no admissible server configuration** that would let it run:

1. The profiler hard-requires `simulate_acc_len == 1.0` on **every DP/TP rank**, for every subcommand, and
2. it hard-refuses `--disable-cuda-graph`.

Therefore the only server config the profiler accepts is `SGLANG_SIMULATE_ACC_LEN=1.0` **with CUDA graphs on**. But at that config the DSpark verify epilogue is installed and its capture hook is appended **with no `simulate_acc_len` guard**, which is the configuration that hangs CUDA-graph capture on multi-node TP (rank desync inside capture). Both halves are true at once ⇒ SPS profiling is unreachable ⇒ no cost table can ever be produced ⇒ `compact` ragged verify (which the runtime itself documents as requiring a table) can never be armed.

Net effect: the entire verify-budget/SPS scheduling family is unreachable on multi-node DSpark TP4, which is precisely the configuration where long-context decode wastes the most verify work (see Impact).

## Source facts (read from the live image)

- `python/sglang/benchmark/dspark_sps_profiler.py`
  - `:80` — `REQUIRED_SIMULATE_ACC_LEN = 1.0`
  - `:458` — `fetch_server_context(...)` asserts `simulate_acc_len == REQUIRED_SIMULATE_ACC_LEN` for **every** server context, regardless of subcommand / mode
  - `:428-433` — the profiler **rejects** `--disable-cuda-graph`
- `python/sglang/srt/speculative/dspark_components/dspark_worker_v2.py:305-327` — the verify epilogue is installed and its capture hook appended whenever
  `(is_compact_mode or static_epilogue_supported) and decode_graph_allowed and is_cuda()`;
  there is **no `simulate_acc_len` term in that condition**
- `python/sglang/srt/speculative/dspark_components/dspark_verify.py:536` — `capture_hook` on the epilogue
- `python/sglang/srt/model_executor/runner/decode_cuda_graph_runner.py:1279-1280` — capture tail hooks are invoked **inside** graph capture

## Steps to reproduce

```bash
# 1. Boot the target with the config the profiler demands
SGLANG_SIMULATE_ACC_LEN=1.0 <normal launch>            # multi-node, TP4, cuda graphs on
# 2. Observe: capture progress line never appears; /health returns 503 while ranks are alive;
#    zero decode lines are emitted; the process must be torn down (see evidence below).

# 3. The profiler additionally refuses the obvious workaround:
python -m sglang.benchmark.dspark_sps_profiler run --disable-cuda-graph
#   -> rejected by the tool itself
```

## Evidence recorded on this deployment

1. **Boot with `SIMULATE_ACC_LEN=1.0` (graphs on):** internal server warmup ran roughly 4× slower (every step advances exactly one token by construction), exceeded the launcher's 600 s read timeout, container exited 1, ranks tore down over the control channel. (This failure mode alone is workaroundable with `--skip-server-warmup`.)
2. **Capture-time hang (the blocking one):** with graphs enabled under that same config, the CUDA-graph capture completion line never appeared, `/health` returned 503 continuously for ~18 minutes with zero decode lines, i.e. capture never completed — consistent with in-graph `DSPARK_ACCEPT_GRAPH` collectives being issued while peers sit in eager out-of-graph syncs (rank desync). Matching source-level diagnosis: the epilogue capture hook is installed with no `simulate_acc_len` guard (`dspark_worker_v2.py:305-327`).
3. **Both halves are individually confirmed in the same image on the same day** (profiler requirement above + unguarded epilogue install above), which is why we did not spend a further 20-minute lane outage reproducing the hang a second time.

## Impact (quantified on real traffic)

On this deployment the draft window is already heavily used at short context and badly wasted at depth. Live decode metrics at the fleet's real long-context traffic (123 k-token context, 1 running request):

```
accept len: 2.20-2.55   accept rate: 0.40-0.52     # deep context
accept len: 3.40        accept rate: 0.80          # short prompts
```

i.e. at depth only **1.2-1.5 of the 3 drafted positions are accepted** and **~half the verified positions per decode step are rejected** — exactly the waste that a profiled verify-budget schedule (`compact` + SPS cost table) exists to trim. Independently, a draft-depth sweep on the same lane showed a wider verify window *wins* at 8 k context (+30 % tokens/s) and *loses* at 96 k (−11 %), because at depth the extra verified positions are mostly rejected: same mechanism, seen from the other side. With the profiler blocked there is no supported way to reclaim it.

## Suggested fixes (any one unblocks profiling)

1. Exclude `simulate_acc_len > 0` from the epilogue-install condition (or keep the epilogue out-of-graph under simulate), so the profiler's required config is capturable; **preferred**, since it fixes the requirement conflict at its root.
2. Allow the profiler to run with CUDA graphs disabled (currently hard-refused) with a loud accuracy warning — the tool already knows how to run against a graphless server in principle.
3. Scope the `simulate_acc_len == 1.0` precondition to single-node/single-rank deployments, where in-graph collectives cannot desync.

## Cross-reference

Same capture-hook machinery as the cap-accept confidence-head failure we hit on the same build (see companion issue): both live behind `model_runner.capture_tail_hooks` invoked at `decode_cuda_graph_runner.py:1279-1280`.