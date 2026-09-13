#!/usr/bin/env python3
"""Decode rate vs live context, with contention averaged out by INTERLEAVING the arms.

The lane is in production, so a serial 8k -> 96k sweep measures whoever else was running, not
context. This round-robins the context sizes rep by rep, uses a long generation (300 tokens) so
the decode rate is not a small-sample artifact, and reports medians per arm.

Prefix is primed first (one call per size) so the measured calls are the real agentic case:
stable cached context + new tokens.

Usage: python3 context-curve-interleaved.py
"""
import json, statistics, time, urllib.request

URL = "http://100.64.0.1:8000/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
FILLER = ("Operational note for the record: the rack stays at twenty-one degrees, the switch "
          "uplinks are aggregated, and the spare SFP is in drawer three. ")
SIZES = [8_000, 32_000, 96_000]
REPS = 3
GEN = 300


def call(messages, max_tokens, tag):
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens,
                       "temperature": 0.0, "stream": True}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    ttft, n, first, last = None, 0, None, None
    with urllib.request.urlopen(req, timeout=900) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data: "):
                continue
            p = line[6:]
            if p == "[DONE]":
                break
            try:
                d = json.loads(p)
            except json.JSONDecodeError:
                continue
            ch = d.get("choices") or []
            if not ch or not ch[0].get("delta", {}).get("content"):
                continue
            now = time.time()
            if ttft is None:
                ttft, first = now - t0, now
            else:
                n += 1
                last = now
    rate = n / (last - first) if (n and last and last > first) else 0.0
    print(f"  {tag:<22} ttft={ttft:6.3f}s  decode={rate:6.1f} tok/s  (n={n})", flush=True)
    return rate


msgs_for = {}
for s in SIZES:
    ctx = FILLER * max(1, s // 28)
    base = [{"role": "system", "content": "You are a terse infrastructure assistant."},
            {"role": "user", "content": ctx + "\n\nReply with the word READY."}]
    msgs_for[s] = base + [{"role": "assistant", "content": "READY"},
                          {"role": "user", "content":
                           "Write 300 short status lines, one per line, each a word and a number."}]
    call(base, 4, f"{s//1000}k prime")

print("\ninterleaved reps (contention hits every arm equally)")
res = {s: [] for s in SIZES}
for rep in range(1, REPS + 1):
    for s in SIZES:
        res[s].append(call(msgs_for[s], GEN, f"{s//1000}k rep{rep}"))

print("\n--- median decode tok/s per context size ---")
for s in SIZES:
    print(f"  {s//1000:>3}k ctx : {statistics.median(res[s]):6.1f} tok/s   "
          f"reps={[round(x,1) for x in res[s]]}")