# TUNING LOG — what we tried, what paid, what is provably dead

Method rule: **audit the source before spending a boot.** Every item below marked DEAD was
killed by reading the pinned build's code (or a cheap live probe), not by a restart.

## 1. The sweep that produced gamma-3

Upstream ships `DSPARK_BLOCK_SIZE=5` (γ=5 ⇒ verify window γ+1=6).

| γ | Round medians (conc1 / conc2 / conc4) | Accept length | Verdict |
|---|---|---|---|
| 5 | 16.9–17.2 / 28.5–34.6 / 42.6–54.7 | 4.2–4.55 | stock |
| 4 | 34.1/37.1/37.4 / 50.0/55.7/56.4 / 68.3/74.3/71.9 | ~2 (synthetic prompts) | +7% conc1, +7% conc2, +3% conc4 vs stock-warm |
| **3** | **34.8/37.8 / 59.5/61.4 / 79.4/78.8** | **3.0–3.5** | **production** |

Stop rule (banked so nobody re-sweeps): when accept length approaches the window, the window
is fully used. At γ=3 accept is 3.0–3.5 of 4 — **γ=2 would clip real accepts**. Sweep closed.
Verify the gamma in the **boot log** (`gamma=3, verify_num_draft_tokens=4`), never the env line
alone. Benign boot-log caveat you will see: `DSpark gamma mismatch: using gamma=N (from
speculative_num_draft_tokens=…) but draft config block_size=…` — the resolved value in the
same log is what counts.

## 2. Warm-up (the biggest single lever after γ)

Cold 16.2 → warm 35–38 at conc1 (≈2×). Plateau ~128 s. This is lane discipline, not a
config knob: `warm-lane.py` post-boot, `keep-warm` heartbeat when idle.

## 3. Dead ends — audited, do not re-burn

| Candidate | Why it is dead |
|---|---|
| `--speculative-attention-mode decode` | Boots clean, zero regression, but **structurally inert**: a single `dsv4` attention backend serves both prefill and decode; the `HybridAttnBackend` that reads the flag is never constructed. Flag removed from env. |
| SPS profiler path (`SGLANG_DSPARK_ENABLE_SPS_RECORD` / `SIMULATE_ACC_LEN=1.0`) | Requires determinism that **hangs cuda-graph capture** under multi-node DSpark; profiler also rejects a graphless server. Table path closed. |
| cap-accept (table-free budgeted verify) | OOM at `mem_fraction_static=0.90`; at 0.85 the OOM is gone but a **deterministic shape crash** fires: `mat1 and mat2 shapes cannot be multiplied (40x4352 and 5376x1)` (`deepseek_v4_dspark.py:1021 compute_confidence` via `dspark_planner.py:267`). Checkpoint head is `[1, 5376]` = 4352+1024 (markov dims) while the draft sampler feeds 4352 = 3328+1024. Not config-fixable — upstream wiring. |
| `--kv-cache-dtype fp4` | Dead; the engine runs **FP8** KV (`fp8_e4m3`) and that is what fits 4M tokens. |
| `--speculative-adaptive` | EAGLE-only path. |
| `--speculative-dspark-align-verify-tokens-to-graph-tier` | Measured **no-op** on this build (kept only because it is harmless). |
| `cutedsl` paged-MQA-logits backend | Documented **SM 100 (Blackwell) only**. GB10 is **SM121** ⇒ gated out at runtime. |
| `enable_quant_communications` | Guarded by `not is_decode_or_idle()` ⇒ **no-op for decode / single-stream**. |
| Fused greedy-Markov sampling kernel | Exists only for `VanillaMarkov` subclasses in `models/dspark.py`; our `DSparkV4MarkovHead` is a standalone `nn.Module` ⇒ **unreachable**. |
| `num_continuous_decode_steps` | Declared in server args, **no consumer** anywhere in `srt/` ⇒ vestigial. |
| `SGLANG_FLASHINFER_MOE_FUSED_FINALIZE` | Only wired for `flashinfer_cutlass`/`cutedsl` MOE runners; ours is `flashinfer_mxfp4` ⇒ never fires. |
| Engram host-table / KV prefetch tuning | Host table already off; NVMe random reads measured 179–287 µs @ 4.4–5.6k IOPS against ~90 ms/step ⇒ **noise**. |
| `DSV41_CACHE_GIB=12` | Head died ~90 s into weight load: host RAM exhausted (A1 has only ~20 GB free at idle). 4 GiB is the fitted value. |
| Live CUDA trace | `/start_profile` works on the running engine but this build emits a **frontend-only trace** (no kernel events), and setting `output_dir` breaks the profiler. Abandoned as a method. |
| TP3 | Not a technical dead end — **owner decision** ("no TP3"), and the 4-node point stands. |
| `enable_fused_moe_sum_all_reduce`, `enable_fused_qk_norm_rope` | Off by default; **not yet tested** (low expected value per the audit — listed so nobody mistakes them for untried wins). |

## 4. Open items

1. **Flat single-stream steady ≈ 52 tok/s** engine-counter vs a 3-node demo's 69. The TP3
   advantage is real (fewer all-reduce legs per verify step); the remaining levers are
   verify-window economics (γ closed) and possibly STS shard calibration.
2. **STS calibration** — the other DSpark lever that does not need the SPS table; its shard
   collection may be viable. Not yet attempted under this recipe.
3. **Keep-warm cadence** — final `KEEP_IDLE_S` decision awaits the clean decay curve.
4. **Upstream issue** — drafted, covering the SPS hang and the cap-accept shape mismatch.
   Publishing is the owner's call.

## 5. Non-obvious fixes worth remembering

- **NCCL / fabric:** per-node active-HCA discovery patch is *required* (mixed HCA names).
- **NFS:** an existing NFSv4 exporter on the head collides on `:2049` → `NFS_SHARE=0` +
  explicit `NFS_VOLUME` fixes it (the release note for this is a one-liner: *don't start a
  second exporter on a port the head already owns*).
- **A4 storage:** home is an XFS-loop symlink; docker resolves symlinks ⇒ set
  `WORKER_ENGRAM_DIR` explicitly.
- **`docker run --rm --entrypoint sh … <<EOF` needs `-i`** or it silently prints nothing.
- **`pkill -f <pattern>` inside an ssh command kills your own ssh session** if the pattern
  text appears in that command line — use a bracket trick (`"foo[-]bar"`) or match `comm`.

## 6. 2026-09-13 window — traps that cost real time

- **`start.sh` forwards an explicit allowlist.** A variable added to `.env.tp4` alone never
  reaches the containers; experiments silently run as no-ops and look like "the flag did
  nothing". Every new experiment variable needs hardcoded `-e VAR=${VAR:-default}` lines in
  `docker_common_args()` **and** `worker_env_lines()`. Published as `patches/0002-*.diff`.
- **Engine logs die with the container.** `docker logs` is gone after a restart; the boot
  script's `serve-*.log` tee captures only a few lines. Harvest the window's `Decode batch`
  rows **before** restarting, or the measurement is unrecoverable.
- **`gpu-state-probe.py` no longer fits alongside the engine.** With `MAX_TOTAL_TOKENS=8M` and
  `MEM_FRACTION_STATIC=0.90`, external CUDA allocations OOM. Probe per node only with the lane
  down (this is the correct method anyway — it removes contention).
- **The lane is shared.** Other agent lanes inject real traffic mid-window (8 concurrent
  requests; 67 prefills/10 min). Label windows and discard contaminated ones; never average
  through them. Use engine-side `#running-req` histograms to prove what the window actually was.
- **A ban can be collateral.** `NCCL_PROTO=^LL128` existed only to shrink pinned host memory;
  the memory win came from the *buffer sizes*, not the protocol ban. Re-testing the ban cost one
  boot and closed the question (+1.7%, noise).
- **Recorders can be structurally blind.** The block-accept estimator only runs when folded
  proposal is off, so its readings never described production. Check the *gate* before believing
  the recorder.

## 7. 2026-09-13 — the communication candidates, closed by source audit (no boot spent)

The v1.1.4 release named four communication candidates. All four are now **unreachable by
configuration** on this build, architecture and checkpoint — verified by reading the running
image, with the exact stop site recorded in `docs/COMM-AUDIT.md`:

| Candidate | Stop site |
|---|---|
| Two-batch overlap | `arg_groups/deepseek_v4_hook.py:280` — hard reject: "DeepSeek-V4.1 does not support two-batch overlap yet" |
| `--enable-fused-moe-sum-all-reduce` | read only by the **Triton** MoE runner (`moe_runner/triton_utils/fused_moe.py:585`); we run `flashinfer_mxfp4` |
| MoE finalize + TP all-reduce fusion (CustomAllReduceV2 push plane) | requires `Mxfp4FlashinferTrtllmMoEMethod` **and** the `flashinfer_trtllm` runner + NVFP4 (`fused_moe_triton/layer.py:465-481`, `mxfp4_flashinfer_trtllm_moe.py:570`); we are Cutlass MXFP4 — the boot log says so: *"deferred finalize is disabled (moe_runner_backend=flashinfer_mxfp4, quant_method=Mxfp4FlashinferCutlassMoEMethod)"* |
| FlashInfer AllReduce fusion (mnnvl/trtllm) | `communicator.py:190-200` requires SM90/SM100; `utils/common.py:295` defines SM100 as capability **major 10**; GB10 is **(12,1)** |
| Quantized (INT8) communications | `arg_groups/validation_hook.py:206` — NPU-only, raises on CUDA |

**New hard rule:** never set `--flashinfer-allreduce-fusion-backend` on GB10. It is not merely
inert — `layers/flashinfer_comm_fusion.py:56` raises `ValueError` from `bootstrap.py:203`, so the
lane would fail to boot and the window would be lost.

**Consequence for the recipe's story:** the 23% all-reduce is not an unturned knob; it is a
property of the MXFP4/Cutlass path on SM121. Reaching it needs an upstream code change, an
SM100-class part, or fewer TP legs (owner-declined). The remaining *configuration* lever is
prefill chunk sizing — `docs/WINDOW-PREFILL-CHUNK.md`.

- **The env file is part of the formula.** The v1.1.5 provenance audit found `env.tp4` was
  missing the durable-fix flags and the four DSpark variables that production runs (metrics,
  mixed-chunk, folded proposal). Patch-application proof on `start.sh`/`Dockerfile` is not
  enough — compare the parsed environment value-for-value before every release
  (`docs/PROVENANCE-AUDIT.md`).