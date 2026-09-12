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
| **gamma-3 = production** | **34.8/37.8** | **59.5/61.4** | — | **79.4/78.8** | ×2 rounds; single rounds to 81 |
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
- Context (configured, live): `context_len=1048576`, `max_total_num_tokens=4000000`,
  KV dtype `fp8_e4m3`, `max_prefill_tokens=16384`, `chunked_prefill_size=4096`,
  `max_running_requests=8`, `available_gpu_mem≈29–32 GB/rank`.
- Boot-log line proving the gamma actually took effect:
  `max_total_num_tokens=4000000, chunked_prefill_size=4096, max_prefill_tokens=16384,
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