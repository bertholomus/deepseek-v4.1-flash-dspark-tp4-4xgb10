#!/usr/bin/env python3
"""Quantile view of the live SGLang /metrics histograms (read-only).

Answers "where does a request actually spend its time": TTFT, inter-token latency,
per-stage latency (chunked_prefill vs prefill_forward vs request_process), queueing,
and the prompt/token size distributions.
"""
import re
import urllib.request

URL = "http://100.64.0.1:8000/metrics"
WANT = ("time_to_first_token_seconds", "inter_token_latency_seconds",
        "e2e_request_latency_seconds", "queue_time_seconds",
        "per_stage_req_latency_seconds", "generation_tokens_histogram",
        "prompt_tokens_histogram", "uncached_prompt_tokens_histogram",
        "eviction_duration_seconds")

raw = urllib.request.urlopen(URL, timeout=30).read().decode()
fam = {}
for line in raw.splitlines():
    if not line or line.startswith("#"):
        continue
    if "{" in line:
        name, rest = line.split("{", 1)
        labels, _, val = rest.rpartition("}")
        labels, val = labels.strip(), val.strip()
    else:
        parts = line.split()
        if len(parts) != 2:
            continue
        name, labels, val = parts[0], "", parts[1]
    if ":" not in name:
        continue
    short = name.split(":", 1)[1]
    if short.endswith("_bucket"):
        short = short[: -len("_bucket")]
    try:
        value = float(val)
    except ValueError:
        continue
    key = (short, re.sub(r',?le="[^"]*"', "", labels).strip(","))
    le = re.search(r'le="([^"]+)"', labels)
    if le:
        bound = float(le.group(1)) if le.group(1) != "+Inf" else float("inf")
        fam.setdefault(key, {}).setdefault("buckets", []).append((bound, value))
    else:
        fam.setdefault(key, {})["meta"] = value
fam = {k: v for k, v in fam.items() if k[0] in WANT}


def quant(buckets, q):
    b = sorted(buckets)
    total = b[-1][1]
    if total <= 0:
        return None
    target = q * total
    prev_le, prev_c = 0.0, 0.0
    for le, c in b:
        if c >= target:
            if c == prev_c or le == float("inf"):
                return le
            frac = (target - prev_c) / (c - prev_c)
            return prev_le + frac * (le - prev_le)
        prev_le, prev_c = le, c
    return None


for (short, labels), d in sorted(fam.items()):
    b = d.get("buckets")
    if not b:
        continue
    tag = re.sub(r'model_name="[^"]*"|engine_type="[^"]*"|moe_ep_rank="[^"]*"|pp_rank="[^"]*"|tp_rank="[^"]*"|',
                 "", labels).strip(", ")
    n = b[-1][1]
    qs = {q: quant(b, q) for q in (0.5, 0.9, 0.99)}
    fmt = " ".join(f"p{int(q*100)}={'%.3f' % v if v is not None else 'n/a'}" for q, v in qs.items())
    unit = "token" if short.endswith("histogram") else "s"
    print(f"{short:32s} n={int(n):6d}  {fmt}  [{unit}] {tag}")