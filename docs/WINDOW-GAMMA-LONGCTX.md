# WINDOW — γ (DSpark draft depth) at fleet-realistic long context

**Status: STAGED 2026-09-14 — awaiting owner GO.** Nothing has run; the lane is on the production
config (`DSPARK_BLOCK_SIZE=3`) and is verified healthy after the 06:04Z incident recovered below.

## Why re-open a sweep we banked as closed

`docs/TUNING-LOG.md` §1 closed γ at **3** on 2026-09-12 (γ=5 stock → 16.9–17.2 conc1; γ=4 → ~34;
γ=3 → 34.8–37.8 conc1, +7–10% at conc2/4). That sweep used **short synthetic prompts** and the
stop rule "accept length approaches the window ⇒ the window is fully used" (accept 3.0–3.5 of 4).

Two things changed since:

1. **The workload changed.** The lane now serves Hermes agent sessions — p50 prompt 52k, p90 148k
   tokens (`docs/TTFT-AND-CACHE.md` §2). The 2026-09-12 sweep was measured before that shape
   dominated.
2. **Peer evidence contradicts the short-prompt ordering at depth.** `joesinvestments/
   DeepSeek-V4-Flash-0731-TP4-4x-DGX-Spark` swept the same knob on 4× DGX Spark at **~105k tokens**
   of real context, one boot per arm (`RECIPE.md`):

   | draft depth | decode @~105k | accept rate |
   |---|---|---|
   | k=3 | 40.6 tok/s | 61.2% |
   | k=5 | 49.3 | 51.9% |
   | **k=7** | **68.6** | 39.4% |

   Acceptance *rate* falls as depth rises; absolute throughput rises anyway, because each
   verification step clears more accepted tokens. That is the opposite of what our short-prompt
   sweep concluded, and it is exactly the regime our fleet now lives in.

So: our γ=3 may be leaving a large multiplier on the table for long-context traffic, or the
2026-09-12 result may hold for our engine. Both are legitimate outcomes; this window settles it.

**Engine caveat, stated up front:** the peer lane is vLLM + DSpark; ours is SGLang + DSpark.
Their kernel stack (`flashinfer_b12x` MoE backend) is not portable to us. This window tests the
**one knob that is comparable** — draft depth — on our engine.

## Hypothesis

At long cached context, deeper drafts amortise better: the draft cost per step grows sublinearly
while accepted tokens per step grow. Predicted: decode tok/s at 96k context rises monotonically
from γ=3 to γ=7; short-context decode may rise less or not at all (the 2026-09-12 result).

Null outcome, equally valuable: no gain ≥ the rule below at 96k ⇒ the 2026-09-12 sweep stands,
γ stays 3, and the peer gain is attributed to their kernel stack, not to depth. That closes the
line for good with a long-context measurement instead of a short-prompt one.

## Design (one variable, three arms, same image)

| | arm C (control) | arm G5 | arm G7 |
|---|---|---|---|
| `DSPARK_BLOCK_SIZE` | **3** (live, no reboot) | 5 | 7 |
| everything else | unchanged | unchanged | unchanged |

Only `DSPARK_BLOCK_SIZE` changes in `.env.tp4`. Boot log must resolve `gamma=<n>` and
`verify_num_draft_tokens=<n+1>` — **the boot log decides whether the arm is the arm we asked
for**, never the env line (`TUNING-LOG.md` §1 caveat: a benign `DSpark gamma mismatch` warning is
expected; the resolved value in the same log is what counts).

Protocol (`tools/gamma-ctx-probe.py`, new):

- Sizes **8k / 32k / 96k**, interleaved rep by rep so fleet contention hits every arm equally
  (same design as `tools/context-curve-interleaved.py`).
- Prefixes are **primed once per size** so measured calls are the real agentic case: stable cached
  context + new tokens. This is a **decode** measurement; cold prefill is not re-litigated here
  (the 2026-09-14 chunk window settled it as a hard floor).
- **Two output styles decide, one annotates**: `prose` and `code` (the two dominant fleet shapes)
  form the primary metric; `list`/counting is reported but never decides, because it flatters
  speculative decoding. Primary = mean of the prose and code medians at 96k.
- 3 reps × 300 generated tokens per call; 600 s per-call timeout; a hung or failed call marks the
  arm **unusable** and the window reverts to control (see incident, below).

## Decision rule (banked before the run)

Adopt an arm iff it beats control by **≥ +8% on the primary metric at 96k** *and* **≥ +3% at 32k**;
among qualifying arms take the highest 96k primary. Otherwise keep **γ=3**. Same conservative
stance as the 2026-09-14 prefill-chunk window (which reverted on a −4.7% that sat inside ±3.2%
boot-to-boot noise).

## Procedure

Unattended runner: **`tools/window-gamma-longctx.sh`** (on spark1, from the recipe dir).

What it does, in order:

1. Assert the lane is healthy; back up `.env.tp4`; raise `maintenance.latch` (watchdog stood down).
2. Arm C on the **live** lane (no reboot): warm → garbled gate → probe.
3. Arm G5: stop → per-node GPU-state annotation (engine down, `WINDOW-PREFILL-CHUNK.md` trap 3) →
   `DSPARK_BLOCK_SIZE=5` → start → assert resolved γ in the boot log → warm → gate → probe.
4. Arm G7: same with 7.
5. Decide by the rule; **settle boot** only if the decision differs from what is live.
6. Always: drop the latch, print env md5 before/after, write `SUMMARY.txt`, harvest `docker logs`
   per arm **before** each restart (logs die with the container, `TUNING-LOG.md` §6).

Evidence dir: `~/dsv41-state/window-gamma-<stamp>/` on spark1 (`runner.log`, `results/<arm>.json`,
per-arm logs/metrics, `SUMMARY.txt`).

Cost: **~50–75 min**, lane dark for most of it (3 boots ≈ 12 min each). Agents served by the lane
fall back per `docs/OPERATIONS.md`.

## Rollback (one copy + one boot)

```bash
cd ~/ai/recipes/DeepSeek-v4.1-Flash-DGX-Sparks
cp ~/dsv41-state/window-gamma-*/.env.tp4.orig .env.tp4     # γ=3 production
systemctl --user reset-failed deepseek-v41-flash-a1a4-sglang-tp4.service
systemctl --user restart deepseek-v41-flash-a1a4-sglang-tp4.service
rm -f ~/ai/runtime/deepseek-v41-flash-a1a4-sglang/maintenance.latch
```

Image untouched; the same shape as the γ, KV-8M and chunk windows already proven on this lane.

## Incident context — why this window carries a hang guard

At ~05:35Z 2026-09-14 a **128k-token cold probe run against the live lane** wedged decode; the
watchdog counted three failed health checks, failed closed and latched, and the lane was dark
05:50–06:16Z. Recovered: gamma=3 confirmed in the boot log, health 200, watchdog healthy, no
latches. The signature matches the open upstream bug **sgl-project/sglang#33549** (DeepSeek-V4 +
DSpark decode forward hangs at deep context, TP, all ranks spinning). Consequences for this window:

- no synthetic probe above **96k** prompts; per-call timeout 600 s; a failed call aborts the arm
  and the window reverts to control;
- the depth probe that tripped it is **not** part of this design.

## Outcome bookkeeping

Result goes into `docs/EVIDENCE.md` (new section) with the window label, then `CHANGELOG.md` as
v1.1.6: either the new `DSPARK_BLOCK_SIZE` with its accept-length evidence, or "γ re-closed at 3,
long-context measurement" — the same discipline that closed engram, NCCL and the acceptance line.