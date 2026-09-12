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
   how much state each step must touch.

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

## Ranked gains for a 4-node lane

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