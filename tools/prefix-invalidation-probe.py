#!/usr/bin/env python3
"""Does a volatile token early in a prompt cost a full re-prefill? (read-only probe)

The radix cache only helps from the first byte that still matches. A timestamp, session id,
reordered tool output or rewritten system block near the TOP of a prompt invalidates the
whole tail behind it, and every turn pays the full cold prefill (measured ~3.5k tok/s).

Two variants of the same 40k-token context, called twice each with a value that changes
between the calls (as a per-turn timestamp does):

  early : volatile marker at the TOP      -> call 2 should miss the cache entirely
  late  : identical context, marker at the END -> call 2 should hit it

TTFT of call 2 is the number that matters: it is what the agent's next turn feels like.
"""
import json
import time
import urllib.request

URL = "http://100.64.0.1:8000/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
FILLER = "the quick brown fox jumps over the lazy dog while the engineer reads a log " * 40
CONTEXT = (FILLER * 250)[:160000]          # ~40k tokens of stable context


def call(prompt, max_tokens=8):
    body = {"model": MODEL, "max_tokens": max_tokens, "temperature": 0, "stream": True,
            "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    ttft = None
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            obj = json.loads(data)
            ch = obj.get("choices") or []
            if ttft is None and ch and (ch[0].get("delta") or {}).get("content"):
                ttft = time.time() - t0
                break
    return ttft


def variant(marker, where, turn):
    """Stable context + a value that changes every turn, placed early or late."""
    m = f"[turn {turn} marker {marker}]\n"
    body = (m + CONTEXT) if where == "early" else (CONTEXT + "\n" + m)
    return ("Read the following context and reply with the single word READY.\n\n"
            "<context>\n" + body + "\n</context>")


for where in ("early", "late"):
    warm = call(variant("aaa", where, 0))         # populate the cache
    t = call(variant("bbb", where, 1))            # next "turn": marker value changed
    print(f"{where:>5}: turn-2 TTFT = {t:6.3f} s   (warm-up call was {warm:.3f} s)")