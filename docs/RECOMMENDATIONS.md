# RECOMMENDATIONS — what would actually make this better

**STATUS UPDATE 2026-09-13 (v1.1.5 audit window):**
- **The acceptance/verify-window lever is CLOSED (negative).** §5 item 2 below was written
  before the measurement landed: the uncapped accept ceiling (~2.66) sits *below* production's
  3.25, and the recorder that produced the "+23%" reading is structurally blind while folded
  proposal is on. Nothing to patch. `docs/EVIDENCE.md` §10.
- **The communication candidates are CLOSED as configuration.** Two-batch overlap, fused
  MoE-sum + all-reduce, MoE finalize + TP all-reduce fusion, FlashInfer all-reduce fusion and
  quantized communications are all unreachable on this build/arch/checkpoint, each with an exact
  stop site. `docs/COMM-AUDIT.md` (and TUNING-LOG §7 — including the one that would have cost a
  failed boot).
- **The remaining configuration lever is prefill chunk sizing**, staged and ready to run in a
  window: `docs/WINDOW-PREFILL-CHUNK.md`. Everything else in §5 is closed or is capacity.
- Provenance: `env.tp4` was found to under-describe production and is fixed;
  `docs/PROVENANCE-AUDIT.md` + the release-checklist rule.

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
- **Prefix cache work** — 98.9 % hit already, so caching *volume* is not a lever. Prefix
  *stability* is: see `docs/TTFT-AND-CACHE.md` (a volatile value placed early in a prompt costs
  a full re-prefill — measured 14.0 s vs 0.59 s TTFT on identical content).
- **Chasing single-stream to 69 tok/s by config** — the lane is latency-bound at ~12× above the
  bandwidth bound; γ was the lever and it is spent. Only a topology change (fewer all-reduce legs,
  i.e. TP3 — rejected by the owner) or a second replica changes that.
- **Per-batch prefill latency "problem"** — an artifact of window-averaged logging (see
  FLEET-BASELINE §measure-like-an-adult). Nothing to fix.

## 5. Where the remaining gains are (measured 2026-09-12, `docs/TTFT-AND-CACHE.md`)

**Revised after direct follow-up measurement.** The first-pass list ranked context hygiene first;
that was falsified (decode is flat from 8k to 96k of context, arms interleaved). Current ranking:

1. **Prefill chunk sizing, retargeted at the measured case (window required, untested).** The
   expensive turns are cold-start requests with 16–43k uncached tokens and **0 cached**
   (`chunked_prefill_size=4096` ⇒ 4–11 sequential chunk-steps ⇒ 4.4–11.6 s TTFT). This is the one
   prefill lever with a measured target; `--schedule-policy lpm` is deprioritised with it, since
   the fleet's uncached tokens are new content rather than missed prefixes.
2. **Acceptance / verify window — an engine defect precedes any tuning.** Ours accepts 2.7–3.4 of
   a 4-token window. The compact (confidence-capped) path that would size the window adaptively
   cannot run on this build: the confidence head is constructed for `hidden+markov` (5376) but is
   fed the draft-stage hidden (4352) and raises at `deepseek_v4_dspark.py:1021`, leaving
   `--speculative-dspark-align-verify-tokens-to-graph-tier` inert. Not config-fixable; a patch
   candidate (upstream-drafted) and the only lever that raises tokens per **step** for every
   stream at once.
3. **Capacity** — 46–52 tok/s alone, ~12–13 with 3–4 streams, 13.7 with 8.
4. **Prefix stability** — a guardrail, not a gain: proven worth 21× per turn if violated, and the
   live fleet does not violate it (94.6 % of prompt tokens cached; the only large uncached turns
   are brand-new sessions with 0 cached tokens).
5. **Context hygiene** — **withdrawn** as a speed lever (falsified; see follow-up 2).

Measured cost of the cold-prefill tax as it stands: **7.1 % of the lane's wall clock**
(21 s per 5-minute window; 79,288 uncached tokens at 3.7k tok/s), p50 turn 1,536 uncached.

Newly **closed** by measurement (do not retry): smaller KV dtype (no fp4 KV path exists in this
image; `fp8_e4m3` is already the smallest), KV capacity (7 % used), γ (2.7–3.4 of a 4-token
window accepted), text-path optimisation (a 39k-token cached prompt costs +0.17 s over a
126-token one), and context trimming for speed.

## 6. Strategic (owner decision, no engineering risk)

- **Capacity, not tuning, is the next ceiling.** Per-stream speed when 8 agents run is 13.7 tok/s
  by arithmetic sharing — no single-engine tuning changes that. If the fleet keeps growing, the
  move is a **second replica** (or a dedicated lane per agent group), which doubles aggregate
  and restores per-stream speed. That is a hardware/placement conversation, not a knob.
- **Publish the recipe** (`NOTICE.md` outlines provenance; licence choice is the owner's) —
  it is rights-clean: config, patches and tooling only, no weights.