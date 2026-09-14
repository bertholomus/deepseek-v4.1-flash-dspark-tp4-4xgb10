# [Bug][DSpark] `cap-accept` boot crashes during CUDA-graph capture: confidence-head linear gets 4352-wide features against the checkpoint's trained `[1, 5376]` weight

**Status:** drafted for upstream filing — not yet posted (owner review pending)
**Filed by:** BertholomusAI (recipe/runtime operator for this deployment)

## Environment

- 4 × NVIDIA GB10, `nnodes=4`, `tp_size=4`, no expert parallel
- DSpark speculative decoding with `SGLANG_RAGGED_VERIFY_MODE=cap-accept`, `mem_fraction_static=0.85`, `DSPARK_BLOCK_SIZE=5` (`gamma=5` ⇒ 40 = `max_bs 8 × gamma 5` rows at the capture call site)
- CUDA graphs enabled; checkpoint: DeepSeek V4.1-Flash DSpark build, 48-shard safetensors

## Summary

`cap-accept` (the confidence-head path, which unlike `compact` needs no profiled cost table) cannot boot on this configuration: weight loading succeeds, then decode CUDA-graph capture raises a matmul shape error at the confidence head, because the features fed at the capture call site are 1024 narrower than the head's trained projection.

## Observed failure

```
RuntimeError: mat1 and mat2 shapes cannot be multiplied (40x4352 and 5376x1)
```

Call chain (all lines read from the running image):

```
decode_cuda_graph_runner.py:1279-1280   capture_tail_hooks[capture_hook](self, out, forward_batch, num_tokens)
draft_worker_common.py:138-149          make_draft_sampler_capture_hook -> draft_sampler(out.hidden_states, input_ids)
dspark_draft_sampler.py:181             self.confidence_fn(draft_hidden=sample_hidden, anchor_tokens=..., draft_tokens=..., confidence_tap=...)
dspark_planner.py:251-273               compute_confidence_tensor -> draft_model.compute_confidence(...)
deepseek_v4_dspark.py:1002-1022         compute_confidence: x_post_hc.view(bs, gamma, -1)
models/dspark.py:437-447                features = torch.cat([hidden_states, markov_embed_stack], dim=-1)  ->  self.proj(features)
models/dspark.py:441                    <-- shape error here
```

The 40 rows confirm this is the captured decode graph at `max_bs=8` with `gamma=5`.

## Ground truth from the checkpoint (read directly, not inferred)

- `mtp.2.confidence_head.proj.weight` → `dtype=BF16`, `shape=[1, 5376]` (tensor map entry → `model-00046-of-00048.safetensors`)
- `config.json` → `text_config.hidden_size = 5120`, `text_config.dspark_markov_rank = 256`, `text_config.num_nextn_predict_layers = 3`
- **5120 + 256 = 5376** ⇒ the head is trained with `with_markov=True` and expects a **5120-wide** hidden state fed alongside a 256-wide markov embedding stack.

## Diagnosis

- The builder is consistent with the checkpoint: `deepseek_v4_dspark.py:546-552` constructs `DSparkConfidenceHead(hidden_size=config.hidden_size, markov_rank=..., with_markov=...)`, i.e. 5120 + 256 = 5376 — matching the trained weight exactly.
- At the capture call site the concatenated width comes out **4352**, which back-solves to a **4096-wide** hidden state (4352 − 256 markov). That is not the 5120-wide post-hidden the head was built and trained for: the capture path supplies the draft sampler's `out.hidden_states`, which is narrower than the model hidden size used to build the head.
- Same eager/capture asymmetry is visible in the code: the eager path (`dspark_worker_v2.py:735` → planner `compute_confidence_tensor`) and the capture path (`draft_worker_common.py:140-149`) reach the same head through different tensors, and only the capture path fails.

So `cap-accept` and (per the companion issue) `compact` are both currently unbootable on this deployment, for different reasons.

## Steps to reproduce

```bash
SGLANG_RAGGED_VERIFY_MODE=cap-accept SGLANG_DSPARK_BLOCK_SIZE=5 \
<normal 4-node TP4 launch with cuda graphs, mem_fraction_static=0.85>
# weights load; decode graph capture begins; RuntimeError: mat1 and mat2 shapes cannot be multiplied (40x4352 and 5376x1)
```

Two earlier attempts on the same build: at `mem_fraction_static=0.90` the boot instead failed in pool allocation (OOM) before reaching capture; at `0.85` it reached capture and produced the shape error above.

## Suggested fixes

1. Feed the capture path the same hidden tensor the eager path feeds (the 5120-wide post-hidden), i.e. route `make_draft_sampler_capture_hook`'s confidence call through the same tap as `compute_confidence_tensor`. **Preferred** — the trained weights then match by construction.
2. If feeding the draft sampler's hidden is intentional, build/load the head against that width — but note this contradicts the checkpoint (`[1, 5376]`), so the checkpoint side would have to change instead.
3. At minimum, assert `hidden_width + markov_rank == proj.in_features` when the head is built, so the mismatch surfaces at load time with a clear message instead of as a capture-time matmul error after a multi-minute boot.

## Cross-reference

Shares the `model_runner.capture_tail_hooks` mechanism (`decode_cuda_graph_runner.py:1279-1280`) with the verify-epilogue capture hook described in the companion issue about SPS profiling.

## Workaround in production

None. `static` ragged-verify mode (confidence head never built) is the only mode that boots on this build; our deployment runs `static` with a fixed verify window and treats `cap-accept`/`compact` as unreachable until this is fixed.