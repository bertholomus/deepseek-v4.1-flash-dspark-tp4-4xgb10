# TTFT AND PREFIX CACHE — where a long-context lane actually spends time

**Measured 2026-09-12 ~23:15 UTC on the live A1–A4 lane, read-only** (`tools/metrics-quantiles.py`,
`tools/ttft-probe.py`, `tools/prefix-invalidation-probe.py`). This is the decomposition the
throughput-only view was hiding, and it changes what "make it faster" means.

## The decomposition

| What | Measured |
|---|---|
| Cached TTFT vs prompt size | 0.347 s @ 126 tok … 0.521 s @ 39,346 tok — **flat** (113× the tokens, +0.17 s) |
| Cold prefill rate | **3.5–3.9k tokens/s** (13,132 uncached → 3.85 s, i.e. ~3.9k tok/s after ~0.5 s fixed; 39,346 → 11.29 s, ~3.65k tok/s) |
| Fixed per-request overhead | ~0.4–0.5 s (present even for a 126-token prompt) |
| Prefix cache | **98.9 %** of prompt tokens cached; median uncached prompt 391 tok |
| Uncached prompt tokens | p50 391 · p90 2,921 · p99 54,280 (≈1 s / ≈15 s of cold prefill) |
| TTFT (streaming) | p50 0.98 s · **p90 6.67 s** · p99 38.4 s |
| Inter-token latency | p50 20 ms · p90 48 ms · p99 98 ms (50 / 21 / 10 tok/s per stream) |
| Accept length | **3.42 of a 4-token window** (85 % of the window used) |
| Live context | `#full token` 344,832 across 3 running requests ≈ **115k tokens per stream** |
| Prompt size | p50 52.5k · p90 148k tokens |
| KV | pool 4M, in use 248k (7 %), queue 0, retractions 0 |

Three conclusions follow, and they are not the ones the throughput numbers suggested:

1. **The text path is not the problem.** Serving a 39k-token prompt costs +0.17 s over serving a
   126-token one when the prefix is cached. Tokenization, chat-template rendering and transfer
   are cheap; do not optimise them.
2. **Uncached tokens are the cost.** Every new token costs ~1/3.7k s of prefill, and TTFT's tail
   is exactly the uncached-token tail. The median turn is fine (~0.4 s); the p90/p99 turns
   dominate.
3. **Long live context is the standing tax.** Decode reads the whole KV of every running stream
   each step, and prefill attention scales with new-tokens × context. At ~115k tokens of context
   per stream, aggregate decode is 63–90 tok/s — not because of any knob, but because that is
   how much state each step must touch. *(Superseded by follow-up 2 below: measured directly,
   context size is NOT the decode lever at this concurrency — the step is latency-bound.)*

## Follow-up measurements (same night) — what survived and what did not

Three follow-ups, all read-only on the live lane. One confirmed, one falsified, one quantified.

**1. Six turns, append-only vs rewritten prefix (CONFIRMED).** Same ~33k-token context, same
content; only the position of a per-turn value differs:

| variant | TTFT by turn (T1…T6) |
|---|---|
| append-only (volatile content appended last) | 0.93 / **0.51 / 0.51 / 0.57 / 0.51 / 0.51** s |
| rewritten prefix (same value rendered at the top) | **11.0 / 11.5 / 11.4 / 10.8 / 11.5 / 11.4** s |

A client that violates this pays ~11 s **every turn, forever** — 21× per turn, and it never
recovers. `tools/context-and-turns-probe.py`.

**2. Context hygiene (FALSIFIED as a speed lever).** Decode rate at a cached prefix and equal
load, arms interleaved so contention hits each equally, 300-token generations, medians of 3:
**8k → 12.4 tok/s, 32k → 11.2, 96k → 12.3**. Twelve times the context changed decode nothing
measurable. Only TTFT moved, mildly (0.36 s → 0.72 s, ≈4 µs/token of prefix matching).
So on this lane the step is latency-bound, not KV-read-bound, and summarising context to chase
speed is wasted work. `tools/context-curve-interleaved.py`. (Re-test if concurrency rises.)

**3. Cold-prefill tax, fleet-only window (QUANTIFIED).** Same 5-minute interval measured two
independent ways — `/metrics` deltas and the engine's own `Prefill batch` lines — agreeing to 2 %
(79,288 vs 77,312 uncached tokens; 7.1 % of wall clock both):

- 13 requests, 1.48 M prompt tokens, **94.6 % cached**, 0 queueing, 0 retractions
- uncached per turn: p50 1,536 · p90 5,888 · max 42,752 (≈0.4 s / 1.6 s / 11.6 s of prefill)
- 46 % of turns ≤1k uncached (healthy appended turns); the two >8k turns had **0 cached tokens**
  — brand-new sessions with a large payload, not re-prefills
- aggregate 100 tok/s fleet-wide

Conclusion: the fleet does **not** violate prefix stability today, so item 1 is a guardrail to
keep (and to re-check when an agent's prompt template changes), not a recovery. The felt tail is
*intrinsic new content*: a cold-start turn with 16–43k uncached tokens waits 4–12 s.

## Revised ranked gains (after the follow-ups)

1. **Prefill chunk sizing, retargeted at the measured case (window required, untested).** The
   expensive turns are cold-start requests with 16–43k uncached tokens and 0 cached, which
   `chunked_prefill_size=4096` splits into 4–11 sequential chunk-steps ⇒ 4.4–11.6 s TTFT. Raising
   the chunk/max-prefill for that shape is the one prefill lever with a measured target.
2. **Acceptance / verify window (engine defect preceding any tuning).** Ours accepts 2.7–3.4 of a
   4-token window; the compact (confidence-capped) path that would size the window adaptively is
   dead on this build — the confidence head is built for `hidden+markov` (5376) but is fed the
   draft-stage hidden (4352), which raises at `deepseek_v4_dspark.py:1021`, so the
   `--speculative-dspark-align-verify-tokens-to-graph-tier` flag is inert. Not config-fixable;
   a patch candidate, upstream-drafted, and the only lever that raises tokens per *step* for
   every stream at once.
3. **Capacity** — per-stream is 46–52 tok/s alone, ~12–13 with 3–4 streams, 13.7 with 8: the only
   measured lever on felt speed at N agents.
4. **Prefix stability** — keep as a guardrail (proven 21× if violated; currently not violated).
5. **Context hygiene** — withdrawn as a speed lever (see follow-up 2).

## The one prompt rule worth ~24×

Same 40k-token context, same content; only the position of a value that changes between turns
differs — a timestamp, session id, re-ordered tool result or re-rendered system block:

| Variant | Turn-2 TTFT |
|---|---|
| volatile marker at the **top** | **14.005 s** |
| identical content, marker at the **end** | **0.594 s** |

The radix cache can only match from the first byte that still agrees. Anything volatile placed
early invalidates the entire tail behind it, and every turn pays a full cold prefill for it.

**Rule: prompts are append-only, and anything that changes every turn goes last.** This needs
no engine change, no window and no configuration — it is prompt construction, and on this lane
it is worth up to 20× the felt TTFT.

## Closed avenues (measured, so they are not retried)

- **Smaller KV dtype.** Supported values in this image: `auto`, `fp8_e5m2`, `fp8_e4m3`,
  `bf16`/`bfloat16`. There is **no fp4 KV path**; `fp8_e4m3` (what we run) is already the
  smallest. `fp8_e5m2` is the same width, so no memory or speed gain.
- **KV capacity.** 4M pool, 7 % used, 98.9 % hit, zero retractions, zero queueing: the pool is
  not a constraint and growing it changes nothing.
- **γ / draft window.** 3.42 of 4 accepted — the window is already sized to the data.
- **Tokenizer / template / transfer optimisation** — see conclusion 1.

## Ranked gains (first pass — superseded by the revised list below)

1. **Context hygiene (free, client side).** ~115k tokens of live context per stream. Decode cost
   and prefill cost both scale with it; no engine setting can undo it. Trimming or summarising
   history to a smaller working set is the single largest available multiplier on felt speed.
2. **Prefix stability (free).** Append-only prompts, volatile content last — proven above.
3. **Engine-side, needs a window (untested on this lane).** `--schedule-policy lpm` (longest
   prefix match: prefer scheduling requests that share a prefix rather than plain FCFS), and
   prefill chunk sizing (`chunked_prefill_size` 4096 / `max_prefill_tokens` 16384) for the p99
   uncached case. Both are prefill-latency levers, not throughput levers.
4. **Capacity.** A second replica remains the only fix for per-stream speed while N agents run —
   the arithmetic sharing of one engine's bandwidth is not tunable.