# FLEET BASELINE — what users of this lane actually get (measured 2026-09-12)

Read-only analysis of 119.7 minutes of live engine log (`fleet-load-analysis.py`, 6,000-line
window; 1,056 decode steps, 908 prefill batches). **No test traffic was generated** — this is
the real production workload of the fleet profiles (our other agent lanes).

## Per-stream decode speed vs concurrency (the felt speed)

| #running-req | samples | median tok/s per stream | p10 | max |
|---|---|---|---|---|
| 1 | 138 | **46.1** | 33.4 | 91.0 |
| 2 | 132 | 36.0 | 28.7 | 53.7 |
| 3 | 157 | 31.4 | 23.7 | 43.5 |
| 4 | 268 | 25.8 | 19.7 | 35.9 |
| 5 | 148 | 22.8 | 17.9 | 28.8 |
| 6 | 111 | 19.9 | 16.1 | 24.0 |
| 7 | 37 | 15.6 | 11.6 | 20.4 |
| 8 | 65 | **13.7** | 11.5 | 16.1 |

Aggregate gen throughput: **median 99.2, max 144.1 tok/s**. Queue depth ≈ 0.06 queued
requests/step (the scheduler is not backing up; latency at high N is *sharing*, not starvation).

## Quality of the speculative path on real traffic

- accept length: **median 2.97** (min 1.80, max 3.73) against the γ-3 window of 4
- accept rate: **median 0.66**
- → the real workload sits close to full window utilisation, same conclusion the γ sweep reached
  on synthetic prompts. This is the evidence that γ-3 is right for *this* traffic, not just for
  the bench harness.

## Caches and context

- **Radix (prefix) cache: 96.4 % hit** — 41.1 M cached of 42.7 M prompt tokens. The fleet shares
  a lot of prompt prefix (system prompts), and the cache is doing its job.
- Context in flight: **median 299,520 tokens, max 660,992** (of the 4 M pool) — these are
  genuinely long-context sessions, and the pool has ~6× headroom at the median.

## Measure-like-an-adult rule (for the next session)

The engine's logged `input throughput` (prefill) is a **rate over the logging window**, so it
includes idle gaps — it reads as low as 8–15 tok/s when the engine is padded with idle time.
Do **not** read it as per-batch prefill latency; an early "prefill is slow" reading was exactly
this artifact and was dropped. For felt speed use `./lane-status.py` (contention-aware) and for
controlled numbers use `tools/bench-normalized.py` (labels CLEAN vs CONTENDED).

## Why the keep-warm heartbeat matters less than we thought

A lane that the fleet uses continuously does not sit at the cold floor — the workload itself is
the warmer. The heartbeat therefore stays **paused** (see EVIDENCE §5) and is kept only as an
option for genuinely idle periods (e.g. overnight batch-free windows).