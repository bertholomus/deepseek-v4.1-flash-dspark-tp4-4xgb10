# EVIDENCE — every number in this recipe, with method and date

Rule: **measured** means a live tool output on A1–A4; **configured** means a value read from
the running engine; **estimated** is labelled as such. If a number is not here, don't quote it.

Per-node hardware state (the hidden GB10 slow state, with the 2026-09-12 A1–A4 measurement) is
in `4-NODE-STATE.md`.

## Harnesses (spark2, `~/`)

| Harness | What it does |
|---|---|
| `tp4_ab.py` | N-round concurrency sweep (conc 1–4), temp 0, fixed prompts, reports client tok/s per round |
| `agentic_single_stream.py` | Single stream, agentic-shaped prompts, reports engine-counter median/peak |
| `quiet_single_stream.py` | Quiet-lane single stream, 800 tok, thinking off, engine-counter samples |
| `batchtest.py` | Garbled-output gate (deterministic prompts, checks token sanity) |
| `prefill_distinct.py <depth> <n>` | Needle-in-haystack long-context gate |

Engine metric = the SGLang `Decode batch … gen throughput` counter at `#running-req: 1`
— the *same counter* third-party 3-node demos quote.

## 1. Baseline and progression (temp 0, medians, client tok/s)

| Stage | conc1 | conc2 | conc3 | conc4 | Notes |
|---|---|---|---|---|---|
| Stock `e59e6eb` (2026-09-11) | 16.9 | 28.5 | 37.6 | 42.6 | first clean boot |
| + PR7 tuning (2026-09-12) | 17.2 | 34.6 | 45.1 | 54.7 | run B: {17.2, 34.6, 45.1, 54.7} |
| Warm lane (plateau 128 s) | 34.2–34.6 | 51.0–53.3 | — | 66.5–74.1 | **warm-up is worth +60–100% @conc1** |
| gamma-4 (`DSPARK_BLOCK_SIZE=4`) | 34.1/37.1/37.4 | 50.0/55.7/56.4 | — | 68.3/74.3/71.9 | ×3 rounds |
| **gamma-3 (production 2026-09-12 → 2026-09-14; superseded by γ=2, §13)** | **34.8/37.8** | **59.5/61.4** | — | **79.4/78.8** | ×2 rounds; single rounds to 81 |
| Cold (post-boot, no warm) | 16.2 | 34.3 | — | 54.7 | why the warmer exists |

Net vs stock: **~2.1× @conc1, ~2.1× @conc2, ~1.85× @conc4.**

## 2. The metric 3rd parties quote (engine counter, single stream)

- `#running-req: 1` decode samples, quiet lane: **median 46.0/49.3/44.3, max 57.9**;
  gamma-4 quiet: medians 45.4–50.9, max 62.4.
- With production traffic alongside: median **~52**, range 38.6–112.6.
- Agentic single stream (γ-3): **medians 54.9–62.6, peaks up to 70.8**, accept length 3.0–3.5.
- Warm plateau `engine_tp` at bs=8: **97.1** (γ-3) vs **101.4** (γ-4) vs **89.0** (γ-5); a
  keep-warm burst measured **108.99**.
- Reference point quoted by a 3× DGX Spark TP3 deployment: **69 tok/s** single stream
  (their own sparkDash counter, decode-only). Our aggregate/multi-stream shape is clearly
  ahead; our *flat single-stream median* is not yet at 69 — open target.

## 3. Correctness gates

- `batchtest.py`: **0 garbled** (run on both gamma-4 and gamma-3).
- Needle-in-haystack: needle at ~136k depth → **`PELICAN-1` exact**; `prompt_tokens=141385`;
  prefill **1773 tok/s** (79.7 s for prefill + 20 generated tokens).
- Context (configured, live): `context_len=1048576`, `max_total_num_tokens=8000000`
  (raised 4,000,000 → 8,000,000 on 2026-09-13; §8),
  KV dtype `fp8_e4m3`, `max_prefill_tokens=16384`, `chunked_prefill_size=4096`,
  `max_running_requests=8`, `available_gpu_mem≈23–32 GB/rank`.
- Boot-log line proving the gamma actually took effect:
  `max_total_num_tokens=8000000, chunked_prefill_size=4096, max_prefill_tokens=16384,
  max_running_requests=8, context_len=1048576` — and per-boot `gamma=3,
  verify_num_draft_tokens=4`. Verify the boot log, never assume the env line won.

## 4. Why this is latency-bound, not bandwidth-bound (arithmetic)

GB10 memory bandwidth ≈ **273 GB/s per node** → 4 nodes ≈ **1092 GB/s aggregate**.
At ~90–110 ms/step decode, we are roughly **12× above** what pure weight streaming would
require. Decode here is dominated by latency (all-reduce legs, verify steps, launch
overheads), which is exactly why the verify window (γ) and warm state were the levers that
paid, and why memory-placement tricks cannot help. **estimated** from vendor bandwidth spec,
not profiler-measured (this build's profiler emits no kernel events — see TUNING-LOG).

## 5. Idle decay — RETRACTED as measured; contention is the confound (2026-09-12)

What we thought: warm single-stream ~45–49 decaying to a floor within tens of minutes, with
one probe at **37.3** ~18 min into idle.

Why it is retracted: the lane has **live production consumers** (tailnet peer `a fleet client`;
fleet profiles our other agent lanes all point at `:8000`). During the
"clean curve" re-run the engine reported `#running-req: 3→8` and `#queue-req: 2` with no
process of ours running, GPU 93 % / 70 °C. Under that contention a 300-token single-stream
request took 18–36 s (8.2–16.7 client tok/s) — i.e. the *same order* as the numbers we were
calling "cold decay". **A single-stream client number on this lane cannot be interpreted
without a contention marker.**

Method that is valid: `tools/bench-normalized.py` — reports client tok/s alongside the engine
decode lines inside the request window (`max #running-req`, engine tok/s at req==1) and labels
each sample CLEAN or CONTENDED. Re-measure before quoting warm/cold behaviour.

Clocks/thermals remain ruled out as the mechanism: ~2405–2444 MHz SM clock, 12 W at idle,
no thermal throttling, and GB10 exposes no clock-locking control. CPU governor
`performance`; C-states ≤433 µs; persistence on.

Heartbeat consequence: `dsv41-keep-warm.timer` is **paused** pending a valid measurement.
Its safety logic is unaffected — `keep-warm.py` derives "idle" from the engine's own
`Decode batch` lines, which include every client, so it stays quiet while the fleet works.

## 6. Fabric

- Two-rail CX7; only PORT_ACTIVE ports are used. Per-node HCA naming is **not uniform**:
  A2 = `mlx5_0`+`mlx5_2`; A3/A4 = `rocep1s0f0`+`roceP2p1s0f0` — hardcoding one name breaks
  the boot (see patch P).
- Nets: head `10.0.0.1`, workers `.2/.3/.4`; second rail `10.0.20.x`.
- Engram random-8K read: A4 5591 IOPS/179 µs, A1 4525, A3 4415, A2 3488 — engram I/O is
  noise at ~90 ms/step (measured 2026-09-12).

## 7. Provenance

Source handoff: `handoffs/20260912-sps-profiling-attempt1.md` (progressive tuning record +
dead-end bank), `handoffs/20260912-engram-mechanisms-study.md`. All measurements taken on
spark1..spark4, 2026-09-11/12, by the operator's agent lane.

## 8. KV pool: 4M → 8M (2026-09-13)

- Raised `MAX_TOTAL_TOKENS` 4,000,000 → 8,000,000 on the live lane; verified in-container
  (`MAX_TOTAL_TOKENS=8000000`), health 200, ping clean.
- **Speed-neutral.** Matched single-stream before/after: no change outside noise — consistent
  with §4 (the step is latency-bound, not KV-read-bound). The pool is a *capability* knob
  (8 concurrent 1M sessions) and a safety margin, not a tok/s knob.
- Rollback: `.env.tp4.pre-kv8m-*` then `reset-failed` + `restart`.
- **Side effect worth knowing:** the larger pool plus `MEM_FRACTION_STATIC=0.90` leaves no
  headroom for an external CUDA probe. `tools/gpu-state-probe.py` now OOMs alongside the engine
  (`cudaErrorMemoryAllocation` on 3 of 4 nodes); per-node slow-state probing needs a maintenance
  window with the lane down. This is a measurement-method cost of the raise, not a serving cost.

## 9. Decode profile: where the step actually goes (2026-09-13)

Method: torch profiler via the engine's own `/start_profile` + `/stop_profile` endpoints during a
596-token single stream (31.3 s), aggregated from a 140 MB Chrome trace, 4.6M events.

| Subsystem | Share of 35.7 s GPU work |
|---|---|
| MoE GEMM (MXFP4, FlashInfer) | ~37% |
| **bf16 RING all-reduce** | **23%** (29,838 calls / 37 s ≈ 800/s, 279 µs each) |
| Dense GEMM | ~11% |
| mHC | ~3.6% |
| Attention | 2.6% |
| Engram gather + hash | 0.6% (230 ms) |

Supporting measurements:
- **Engram is not the bottleneck.** NVMe ~12% utilisation, ~1,200 read IOPS, 0.2–0.4 ms latency,
  iowait ~1%. Cache 2 GiB/layer (16-way); hit rate 84–86% single-stream, 67.7% at 4 streams.
  Per-layer counters (engine log): `lookups=221,245 hit_rate=84.2% reads=34,971` (layer 1);
  `lookups=516,775 hit_rate=83.4% reads=85,828` (layer 14). Worst observed minute: 79.8% hit,
  21,901–85,874 reads/min for a layer.
- **SM was 94% busy during decode**, and GB10 does not expose memory-controller counters via
  `dmon`, so a bandwidth-bound claim was **not** made from that probe.
- **The classic quantization prize is absent**: routed experts already ship MXFP4 4.25 bpw (QAT'd
  upstream by DeepSeek) ⇒ ~57% of the checkpoint is already 4-bit. The remaining FP8 is the
  engram tables (~189 GiB) plus ~9 GiB of dense/attention weights.

## 10. Acceptance line — CLOSED as an artifact (2026-09-13)

- Uncapped accept-length, cumulative over **15,689 blocks: ≈ 2.66**. Production already runs
  **3.25**. The "ceiling" sits *below* current performance, and the cap thresholds were already
  open at 1.0 — so a confidence-head patch had nothing to deliver.
- Folded-off arm (`SGLANG_DSPARK_FOLDED_PROPOSAL=0`): observed accept 2.84 mean vs production
  3.25, and matched single-stream ~**9% slower** (predicted 14%). Folded proposal is confirmed
  the right production choice.
- Root cause of the bogus "+23%" reading: `dspark_observability.py` only feeds the block-accept
  recorder when the proposal is **not** folded, so the proposal path is structurally invisible.
- The estimator recorder stays enabled (inert on the fast path, free re-profiling later). The
  `start.sh` passthrough published as `patches/0002` is what lets this be re-run at all.

## 11. NCCL LL128 A/B — CLOSED as a no-op (2026-09-13)

The recipe ships `NCCL_PROTO=^LL128` purely to hold pinned host memory at 0.14 GiB instead of
4.7 GiB (default NCCL allocates 512 buffers × 9.19 MiB for Simple + LL128 + LL). With the small
buffers kept (`NCCL_BUFFSIZE=1 MiB`, `NCCL_LL128_BUFFSIZE=256 KiB`), re-allowing LL128 costs only
~128 MiB pinned, so the ban was re-tested directly:

| Arm | Single-stream client median (3 × 1000 tok) |
|---|---|
| `NCCL_PROTO=^LL128` (baseline) | 35.5 tok/s (27.0 / 36.5 / 35.5) |
| `NCCL_PROTO=LL,LL128,Simple` | 36.1 tok/s (36.1 / 37.2 / 35.3) |

**+1.7% — inside run-to-run noise.** Engine-side counter agreed (~37 tok/s both boots).
Two windows were labelled contaminated and discarded (an 8-concurrent fleet spike; a
67-prefills/10-min cadence from another agent lane). Conclusion: the low-latency protocol is not
where the 23% all-reduce cost lives — the cost is structural (one small transfer per layer per
step, ~800/s).

## 12. Hardware note — firmware is not uniform across the four nodes (2026-09-13)

| Node | Driver / GSP | VBIOS | System BIOS | Kernel |
|---|---|---|---|---|
| 1, 2 | 580.173.02 | 9A.0B.25.00.00 | GX10DGX.0105.2026.0505.1153 | 6.17.0-1029-nvidia |
| 3, 4 | 580.159.03 | 9A.0B.1E.00.00 | GX10DGX.0104.2026.0326.1657 | 6.17.0-1026-nvidia |

A clean 2+2 split. In TP4 lockstep the slowest rank sets the step time, so non-uniform
driver/GSP/VBIOS is a standing suspect for tail latency. **No throughput cost has been
demonstrated**, and firmware was deliberately not updated mid-window. One node measured
245.7 GB/s p50 in the decode-shaped GEMV probe ("fast" state) before memory pressure blocked the
other three; a full four-node slow-state sweep needs a maintenance window (§8).

## 13. Fair-go window (2026-09-14) — γ 3 → 2 ADOPTED; two nulls; method correction

Design, arms and the pre-registered rule (fixed before launch): `WINDOW-FAIRGO.md`. Runner
`tools/window-fairgo.sh`, window `window-fairgo-20260914T214105Z`, 4 boots + 6 probes, ~92 min,
latch raised throughout.

Rule: fleet-weighted `W = 0.20·P8 + 0.30·P32 + 0.50·P96` (fleet traffic is long-context
dominant); adopt iff `W ≥ +5%` vs mean of the two bracketing controls **and** every depth ≥ 92% of
control **and** zero probe failures **and** a clean garbled-output gate. Drift flag: |C2 − C1| >
15% at any depth forces an arm to clear **both** controls at that depth.

| arm | config | 8k | 32k | 96k | W | verdict |
|---|---|---|---|---|---|---|
| C1 | γ=3, live config (opening control) | 12.96 | 12.79 | 13.05 | 12.95 | — |
| **G2** | **γ=2** | **19.99** | **19.71** | **19.77** | **19.80** | **ADOPTED** |
| F1 | `FUSED_GREEDY_MARKOV=1` | 14.27 | 12.62 | 13.12 | 13.20 | null (−15.2%) |
| L | `--schedule-policy lpm` | 18.22 | 18.16 | 18.23 | 18.21 | null vs warm control |
| C2 | γ=3 restored (closing control) | 17.83 | 18.52 | 18.09 | 18.17 | — |
| SETTLE | γ=2 after adoption | 19.84 | 19.26 | 19.45 | 19.47 | confirms +4.0…+11.3% |

PRIMARY = prose+code median client tok/s, temp 0, 300-token generations, per-arm salted prefixes;
`list` style recorded in the JSON. Gate: 6/6 arms `garbled=0`.

**The drift flag fired, and it decided the window.** C2 vs C1: +37.6% / +44.8% / +38.6% — the lane
got materially faster mid-window as fleet contention eased. Against the *closing* control: γ=2
+12.1 / +6.4 / +9.3%, `lpm` +2.4 / −1.9 / +0.8%, fused-greedy −20.0 / −31.9 / −27.5%.

So: **`lpm` is a no-op that a mean-based rule alone would have credited with +17%.** γ=2 is the
only arm clearing both controls at every depth, and it clears them again on an independent settle
boot. Bracketing controls are the reason this window can *adopt* anything.

**Method correction (banked, re-reads past windows):** the packed/engram prefix cache survives an
engine restart, so later arms in a multi-boot window inherit warm prefixes and arm order leaks into
the numbers (the 2026-09-14 γ-window settle arm: 18.5 tok/s against a 10.6 control, same window).
The fair-go probe salts each arm's prefix — `tools/patch-fairgo-probe.py` (`--salt`). Earlier
verdicts are conservative in the direction that matters: the winners there ran warmest.

**Hypothesis closed at source, no boot spent:** `speculative_accept_threshold_single/_acc` are
consumed only by the dflash/eagle paths (`dflash_utils.py:916`, `eagle_utils.py:907`) — DSPARK has
no engine-provided adaptive-depth knob to arm.

**Final state:** settle boot asserted `gamma=2, verify_num_draft_tokens=3`; health 200; watchdog
healthy, 0 failures, not latched; latch dropped; env md5 `6add2ff5529e0d39b4e0bd7311e3a840`
(value-identical to `env.tp4.as-deployed-20260914`; pre-window `94a97d96738e8b1e0a4d4093c59010fa`).
