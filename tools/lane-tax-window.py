#!/usr/bin/env python3
"""What does the cold-prefill tax actually cost us per hour? (read-only, no load)

Takes two /metrics snapshots WINDOW seconds apart and reports the deltas: uncached prompt
tokens, the prefill seconds they buy (at the measured 3.5-3.9k tok/s), requests served, decode
throughput, accept length, queueing, retractions, and per-stage prefill/decode latency shares.

Usage: python3 lane-tax-window.py [WINDOW_SECONDS]   (default 420)
"""
import sys, time, urllib.request

METRICS = "http://100.64.0.1:8000/metrics"
WINDOW = int(sys.argv[1]) if len(sys.argv) > 1 else 420
PREFILL_TOK_S = 3.7e3   # measured: tools/ttft-probe.py

WANT_COUNTER = ["prompt_tokens_total", "cached_tokens_total", "generation_tokens_total",
                "prefill_effective_tokens_total", "num_requests_total", "http_requests_total",
                "evicted_tokens_total", "num_retracted_reqs"]
WANT_GAUGE = ["cache_hit_rate", "spec_accept_length", "spec_accept_rate", "num_running_reqs",
              "num_queue_reqs", "kv_used_tokens", "gen_throughput", "decode_sum_seq_lens"]
WANT_HIST = ["per_stage_req_latency_seconds", "time_to_first_token_seconds"]


def snap():
    raw = urllib.request.urlopen(METRICS, timeout=30).read().decode()
    vals, sums = {}, {}
    for line in raw.splitlines():
        if not line or line.startswith("#") or "{" not in line:
            continue
        name, rest = line.split("{", 1)
        labels, value = rest.rsplit("}", 1)
        short = name.split(":", 1)[1]
        try:
            v = float(value.strip())
        except ValueError:
            continue
        base = short[:-len("_bucket")] if short.endswith("_bucket") else short
        key = f"{base}|{labels.replace(chr(34), '')}"
        if base in WANT_HIST:
            if short.endswith("_bucket"):
                sums[key] = sums.get(key, 0.0) + v
            else:
                vals[base] = vals.get(base, 0.0) + v
        elif base in WANT_COUNTER or base in WANT_GAUGE:
            vals[key if base in WANT_COUNTER else base] = \
                vals.get(key if base in WANT_COUNTER else base, 0.0) + v
    return vals, sums


print(f"sampling {WINDOW}s ...")
t0 = time.time()
a, _ = snap()
time.sleep(WINDOW)
b, _ = snap()
wall = time.time() - t0


def d(key):
    return b.get(key, 0.0) - a.get(key, 0.0)


prompt = sum(v for k, v in b.items() if k.startswith("prompt_tokens_total|")) - \
         sum(v for k, v in a.items() if k.startswith("prompt_tokens_total|"))
cached = sum(v for k, v in b.items() if k.startswith("cached_tokens_total|")) - \
         sum(v for k, v in a.items() if k.startswith("cached_tokens_total|"))
reqs = sum(v for k, v in b.items() if k.startswith("num_requests_total|")) - \
       sum(v for k, v in a.items() if k.startswith("num_requests_total|"))
gen = sum(v for k, v in b.items() if k.startswith("generation_tokens_total|")) - \
      sum(v for k, v in a.items() if k.startswith("generation_tokens_total|"))
uncached = max(prompt - cached, 0.0)
prefill_s = uncached / PREFILL_TOK_S

print(f"\nwindow {wall:.0f}s of live production traffic")
print(f"  requests served           : {reqs:.0f}")
print(f"  prompt tokens             : {prompt:,.0f}")
print(f"  cached (radix) tokens     : {cached:,.0f}  ({100*cached/prompt if prompt else 0:.1f} %)")
print(f"  UNCACHED tokens           : {uncached:,.0f}")
print(f"  cold prefill time bought  : {prefill_s:.0f}s  ({100*prefill_s/wall:.1f} % of wall clock)")
print(f"  as a rate                 : {3600*prefill_s/wall:.0f} s of cold prefill per hour")
print(f"  generation tokens         : {gen:,.0f}  ({gen/wall:.1f} tok/s aggregate, fleet-wide)")
print(f"  per request               : {uncached/reqs if reqs else 0:,.0f} uncached tok, "
      f"{gen/reqs if reqs else 0:.0f} generated tok")
print("\n  gauges (now): " + ", ".join(
    f"{k}={b.get(k, 0):.3g}" for k in WANT_GAUGE if k in b))