# RECOMMENDATIONS — what would actually make this better

**STATUS UPDATE 2026-09-12 (durable-fix window executed):**
- ~~§1 mixed-chunk A/B~~ — **DONE, kept** (`enable_mixed_chunk=True` verified in resolved
  args; interleave witness + plateau 113.34). See `DURABLEFIX-RESULT.md`.
- §1b γ at high concurrency — γ is spent, stays closed.
- §3 scheduler policy — queue depth remains ~0, stays closed.
- Metrics gap — **CLOSED** (`--enable-metrics` live, 447 `sglang:*` series on `/metrics`).
- New tools `batchtest.py` / `prefill_distinct.py` installed in the lane; `verify.sh` re-pointed
  at the lane head so all five gates run green in one command.

Ranked by (impact on the fleet's felt experience) ÷ (risk to a lane that is in production use).
Every claim here is tied to a measurement in `FLEET-BASELINE.md` or `EVIDENCE.md`.

## 0. Done already (zero risk, no outage)

- **`lane-status.py`** — one command answers "slow, or just busy?" (health, running-req, queue,
  accept len, aggregate, per-stream estimate, verdict). Installed in the lane dir. This exists
  because a whole morning of measurement was misread as "idle decay" when it was fleet traffic.
- **`bench-normalized.py`** — every single-stream sample is labelled CLEAN or CONTENDED.
- **`fleet-load-analysis.py`** / **`prefill-deepdive.py`** — read-only log analysis; no traffic.
- **keep-warm heartbeat demoted to optional** (paused). The fleet's own traffic keeps the lane
  warm; the heartbeat only has a role in genuinely idle windows.
- **`FLEET-BASELINE.md`** — the real, measured felt-speed curve, so future sessions stop
  re-deriving what "good" looks like.
- **`gpu-state-probe.py` + `4-NODE-STATE.md`** — catches the one failure mode unique to a 4-node
  recipe: a single Spark in the hidden GB10 slow state (memory bandwidth 3.3× down, invisible to
  `nvidia-smi`), which TP4 lockstep turns into a fleet-wide slowdown. All four nodes measured
  fast (≈247 GB/s p50) on 2026-09-12. Run the probe before believing any slowdown is a tuning
  problem.

## 1. Cheap experiment: mixed chunked prefill (needs a window ~20 min) — highest untested value

**204 of 908** prefill batches land within 1 s of a decode step — the workload genuinely
interleaves prefill with decode. `--enable-mixed-chunk` lets a prefill chunk share a step with
decode instead of stalling the decode streams. Previously dismissed as a "prefill-batch lever,
irrelevant to conc1 decode" — that reasoning was single-stream reasoning; on a **shared** lane it
is the exact case that matters. A/B it, keep it only if the latency distribution improves.
Expected gain: modest, but it targets the one measured shape the recipe never addressed.

## 1b. γ at high concurrency (req 5–8) — low expected value, do only if a window happens anyway

An earlier draft of this doc ranked "re-tune γ for batched traffic" first, on the theory that
verify tokens are cheap at high batch (step cost is dominated by KV read + all-reduce, so extra
verify positions cost little) and might therefore favour a *larger* γ than the single-stream
optimum. **Our own data argues against that:** the γ-4 vs γ-3 A/B ran at conc 2 and 4 (batched,
not single-stream) and γ-3 won there (conc2 59.5/61.4 vs 50.0/55.7; conc4 79.4/78.8 vs
68.3/74.3). Fleet traffic does sit near the γ-3 window edge (accept 2.97 of 4), but the honest
read is that **γ is spent**. Only the req 5–8 regime was never benched; treat any test there as
curiosity, not a plan, and never at the cost of a production outage.

## 3. Cheap experiment: scheduler policy (lowest value)

`--schedule-policy` (fcfs / lpm / …) and `--schedule-conservativeness` were never tested under
multi-tenant bursty load. Queue depth is ~0 today (mean 0.06 queued req/step), so the scheduler
is not the bottleneck; the value would be latency smoothing at best.

## 4. Not worth doing (measured, so future sessions don't retry)

- **Raising the KV pool / `MEM_FRACTION_STATIC`** — context in flight peaks at 661k of the 4 M
  pool (median 300k). No pressure.
- **Prefix cache work** — 96.4 % hit already.
- **Chasing single-stream to 69 tok/s by config** — the lane is latency-bound at ~12× above the
  bandwidth bound; γ was the lever and it is spent. Only a topology change (fewer all-reduce legs,
  i.e. TP3 — rejected by the owner) or a second replica changes that.
- **Per-batch prefill latency "problem"** — an artifact of window-averaged logging (see
  FLEET-BASELINE §measure-like-an-adult). Nothing to fix.

## 5. Strategic (owner decision, no engineering risk)

- **Capacity, not tuning, is the next ceiling.** Per-stream speed when 8 agents run is 13.7 tok/s
  by arithmetic sharing — no single-engine tuning changes that. If the fleet keeps growing, the
  move is a **second replica** (or a dedicated lane per agent group), which doubles aggregate
  and restores per-stream speed. That is a hardware/placement conversation, not a knob.
- **Publish the recipe** (`NOTICE.md` outlines provenance; licence choice is the owner's) —
  it is rights-clean: config, patches and tooling only, no weights.