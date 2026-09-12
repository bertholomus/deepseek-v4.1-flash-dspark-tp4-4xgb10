#!/usr/bin/env python3
"""Where does TTFT actually go on our lane? (read-only probe, production endpoint)

Radix cache hit rate is 98.9% and the median uncached prompt is only ~390 tokens, yet
median TTFT is ~1 s. That means most of TTFT is NOT prefill compute. This probe separates
the candidates by holding the fillers identical (so the prefix cache hits after rep 1) and
varying only the prompt *text* the server must receive, tokenize and template:

  size   : prompt length in characters
  cold   : first call (no cache)
  cached : repeat call on the same prefix (radix cache hit)

If TTFT grows with prompt size even when cached, the cost is the text path
(HTTP bytes + tokenize + chat template + scheduling the cached prefix), which the fleet's
own prompt hygiene can fix. If TTFT is flat in size, it is engine-side fixed overhead.
"""
import json
import time
import urllib.request

URL = "http://100.64.0.1:8000/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
FILLER = ("the quick brown fox jumps over the lazy dog while the engineer reads a log " * 20)


def prompt_of(chars):
    body = (FILLER * (chars // len(FILLER) + 1))[:chars]
    return ("Read the following context and reply with the single word READY.\n\n<context>\n"
            + body + "\n</context>")


def call(prompt, max_tokens=8):
    payload = {"model": MODEL, "max_tokens": max_tokens, "temperature": 0,
               "stream": True, "stream_options": {"include_usage": True},
               "messages": [{"role": "user", "content": prompt}]}
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = json_tokens = None
    t0 = time.time()
    ttft, usage = None, None
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            obj = json.loads(data)
            if obj.get("usage"):
                usage = obj["usage"]
            ch = obj.get("choices") or []
            if ttft is None and ch and (ch[0].get("delta") or {}).get("content"):
                ttft = time.time() - t0
    total = time.time() - t0
    return ttft, total, usage


SIZES = [512, 4096, 16384, 65536, 196608]
print(f"{'chars':>8} {'prompt_tok':>10} {'cached_tok':>10} {'TTFT_cold':>10} {'TTFT_cached':>11} {'e2e_cached':>10} {'decode_tok/s':>12}")
for chars in SIZES:
    p = prompt_of(chars)
    try:
        ttft1, tot1, u1 = call(p)
        ttft2, tot2, u2 = call(p)
        ttft3, tot3, u3 = call(p)
        ptok = (u2 or {}).get("prompt_tokens")
        cdet = (u2 or {}).get("prompt_tokens_details", {})
        cached = (cdet or {}).get("cached_tokens")
        gen = (u2 or {}).get("completion_tokens") or 1
        dtps = gen / max(tot2 - (ttft2 or 0), 1e-3)
        print(f"{chars:>8} {ptok if ptok is not None else -1:>10} "
              f"{cached if cached is not None else -1:>10} "
              f"{ttft1:>10.3f} {min(ttft2, ttft3):>11.3f} {tot2:>10.3f} {dtps:>12.1f}")
    except Exception as exc:
        print(f"{chars:>8}  FAILED {type(exc).__name__}: {exc}")