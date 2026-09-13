# COMM AUDIT — the all-reduce candidates, closed by reading the pinned build

**Date:** 2026-09-13 · **Method:** source audit of the running image, no boot spent
(the repo's rule: *audit the source before spending a boot*). Every line number below is from
the container `dsv41-head`, image `dsv41-4x-spark:local`, build `dev-dsv41` — the exact stack the
recipe ships.

**Result:** of the six communication candidates that were "named, not proven" at v1.1.4, **none
is reachable by configuration on this build, architecture and checkpoint.** The 23% all-reduce
is not a knob we have not turned yet; it is a code-level property of the MXFP4/Cutlass path on
SM121. The remaining configuration lever in this recipe is **prefill chunk sizing**
(`docs/WINDOW-PREFILL-CHUNK.md`), and the remaining speed lever overall is capacity (a second
replica), not tuning.

Paths below are relative to `/sgl-workspace/sglang/python/sglang/srt/`.

## Live configuration the audit is against

Resolved `server_args` from the running engine's boot log (`docker logs dsv41-head`):

```
moe_runner_backend = flashinfer_mxfp4        speculative_algorithm = DSPARK
enable_dp_attention = False                  disable_custom_all_reduce = False
enable_two_batch_overlap = False             enable_quant_communications = False
flashinfer_allreduce_fusion_backend = None   enable_fused_moe_sum_all_reduce = False
enable_mixed_chunk = True                    chunked_prefill_size = 4096
max_prefill_tokens = 16384                   mem_fraction_static = 0.9
```

Boot-log fingerprint of the MoE path:

```
FlashInfer TRTLLM MoE deferred finalize is disabled
  (moe_runner_backend=flashinfer_mxfp4, quant_method=Mxfp4FlashinferCutlassMoEMethod).
```

## The candidates

| # | Candidate | Verdict | Evidence (exact site) |
|---|---|---|---|
| 1 | Two-batch overlap (hide one ubatch's all-reduce behind the other's compute) | **Dead — hard boot failure** | `arg_groups/deepseek_v4_hook.py:280` lists `("two-batch overlap", cfg.enable_two_batch_overlap)` in the `unsupported` tuple that raises `"DeepSeek-V4.1 does not support {feature} yet; disable it to serve this model."` The model hook rejects it before any kernel is chosen. |
| 2 | `--enable-fused-moe-sum-all-reduce` | **Dead — no consumer in this configuration** | The flag is read **only** by the Triton MoE runner: `layers/moe/moe_runner/triton_utils/fused_moe.py:585,820,830,858,875`. The resolved runner here is `flashinfer_mxfp4` (boot log above), so the fused path is never constructed. |
| 3 | MoE **finalize + TP all-reduce** fusion over the `CustomAllReduceV2` push plane (the interesting one: it fuses the 23% transfer into the MoE epilogue) | **Dead — the required pairing is not ours** | `should_use_fuse_finalize_all_reduce()` (`layers/quantization/mxfp4_flashinfer_trtllm_moe.py:570`) requires `isinstance(experts.quant_method, Mxfp4FlashinferTrtllmMoEMethod)`, and the layer-level gate (`layers/moe/fused_moe_triton/layer.py:465-481`) requires `get_moe_runner_backend().is_flashinfer_trtllm()` **with** `SGLANG_ENABLE_MOE_DEFERRED_FINALIZE` + `ModelOptNvFp4FusedMoEMethod` (NVFP4) or the Qwen3.5 FP8 path. Our resolved pair is the other one — runner `flashinfer_mxfp4`, quant method `Mxfp4FlashinferCutlassMoEMethod` — so both halves of the gate are false. The engine says so itself at boot (fingerprint above). |
| 4 | FlashInfer AllReduce fusion (`mnnvl` / `trtllm`) | **Dead on SM121 — and forcing it fails the boot** | Application gate: `layers/communicator.py:190-200` → `apply_flashinfer_allreduce_fusion` requires `_is_sm90_supported or _is_sm100_supported`. `utils/common.py:295` defines `is_sm100_supported` as device capability **major == 10** (SM120/SM121 are explicitly excluded elsewhere in the same file); GB10 reports capability **(12, 1)**. The auto-enable pass also deliberately suppresses itself for our exact topology — `arg_groups/overrides.py:969-1017`, `prefer_custom_dsv41` (model_type `deepseek_v41` + `hidden_size==5120` + Blackwell + `tp_size==4` + 1 node + custom AR on) — because "V4.1 TP4 uses the custom push plane for decode and fused MoE finalize". **Do not set `--flashinfer-allreduce-fusion-backend` explicitly on GB10:** `layers/flashinfer_comm_fusion.py:56` `_resolve_backend()` raises `ValueError("FlashInfer allreduce fusion requires SM90 or SM10X NVIDIA GPUs.")`, and it is called from `distributed/bootstrap.py:203` — the lane would fail to boot. |
| 5 | Quantized (INT8) TP communications | **Dead — NPU-only** | `arg_groups/validation_hook.py:206`: `enable_quant_communications and cfg.device != "npu"` → `ValueError("Communications quantization is only supported for NPU device")`. (The v1.1.4 note "no-op for decode" was gentler than the truth: it cannot be enabled at all.) |
| 6 | `--enable-fused-qk-norm-rope` | **Dead — other architectures** | Read only by `models/mellum.py:234` and `models/qwen3_moe.py:523`. Not reachable for `deepseek_v41`. |
| 7 | NCCL protocol (`^LL128` vs `LL,LL128,Simple`) | **Closed by measurement (v1.1.4)** | `docs/EVIDENCE.md` §11: +1.7%, inside run-to-run noise. |

## What this closes, and what it does not

- **Closes** the "comm" line as a *configuration* question. Three of the seven candidates were
  never tested because they could never have run; one of them (#4, forced) would have cost a
  failed boot had anyone tried it blind. That is the value of the audit: it converts four
  "untested" items into four "unreachable, with the exact line that stops it" items.
- **Does not close** the cost. 800 all-reduce calls/s × 279 µs is real. Reaching it needs one of:
  * an upstream change enabling the MXFP4/Cutlass path on any of the routes above (the
    finalize+AR fusion of #3 is the most promising, because it targets exactly this transfer),
  * SM100-class datacenter Blackwell rather than GB10 (fusion backends are `major==10`),
  * or fewer TP legs (TP3 — an owner decision, already declined).
- **Do not re-audit these.** If a future session wants to spend a boot here, the honest prior is
  "the code path does not exist for this configuration", not "this flag has not been tried".

## Reproduce this audit

```bash
ssh spark1 'docker exec dsv41-head sh -lc "
  SG=/sgl-workspace/sglang/python/sglang/srt
  grep -rn \"fused_moe_sum_all_reduce\" \$SG --include=*.py | grep -v server_args
  grep -rn \"def is_sm100_supported\" -A5 \$SG/utils/common.py
  sed -n 190,200p \$SG/layers/communicator.py
  sed -n 570,600p \$SG/layers/quantization/mxfp4_flashinfer_trtllm_moe.py
  ps aux | grep -o \"moe-runner-backend [a-z_]*\"
"'
# and the resolved args, from the engine's own boot line:
ssh spark1 'docker logs dsv41-head 2>&1 | grep -m1 "server_args="'
```