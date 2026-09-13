#!/usr/bin/env python3
"""Two client-side measurements on the live lane, read-only, no engine change.

A) CONTEXT-COST CURVE: with the prefix already cached (the real agentic case - context is
   stable, only the new turn is uncached), how much does live context cost decode throughput?
   Same question the fleet faces: 115k tokens of context per stream.

B) APPEND-ONLY PROOF over 6 turns: a growing conversation where the newly appended turn is the
   only new content must keep TTFT flat; a conversation that re-renders its prefix must not.

Usage: python3 context-and-turns-probe.py
"""
import json, time, urllib.request

URL = "http://100.64.0.1:8000/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
FILLER = ("Operational note for the record: the rack stays at twenty-one degrees, the switch "
          "uplinks are aggregated, and the spare SFP is in drawer three. ")


def call(messages, max_tokens=160, tag=""):
    """Streamed call; returns (ttft_s, decode_tok_s, prompt_tokens, completion_tokens)."""
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens,
                       "temperature": 0.0, "stream": True}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    ttft = None
    first = 0
    last = None
    n = 0
    pt = ct = None
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                d = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if d.get("usage"):
                pt = d["usage"].get("prompt_tokens", pt)
                ct = d["usage"].get("completion_tokens", ct)
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
    print(f"  {tag:<28} ttft={ttft if ttft is None else round(ttft,3)}s  "
          f"decode={rate:5.1f} tok/s  prompt_tok={pt}  gen_tok={ct}")
    return ttft, rate, pt, ct


# --- A) context-cost curve: cached prefix, measure the decode that follows -------------
print("A) CONTEXT-COST CURVE (cached prefix: context stable, only the tail is new)")
for target in (8_000, 32_000, 96_000):
    words = max(1, target // 28)
    ctx = FILLER * words
    msgs = [{"role": "system", "content": "You are a terse infrastructure assistant."},
            {"role": "user", "content": ctx + "\n\nReply with the word READY."}]
    call(msgs, max_tokens=6, tag=f"{target//1000}k warm-cache fill")   # prime the prefix
    msgs2 = msgs + [{"role": "assistant", "content": "READY"},
                    {"role": "user", "content": "Now list 30 one-word status codes separated by spaces."}]
    for rep in (1, 2):
        call(msgs2, max_tokens=140, tag=f"{target//1000}k ctx rep{rep}")

# --- B) append-only vs re-rendered prefix over 6 turns ---------------------------------
print("\nB) SIX-TURN TURN-LATENCY (identical content; only prefix handling differs)")
BASE = FILLER * 1200          # ~33k tokens of stable operational context
for variant in ("append-only", "rewritten-prefix"):
    hist = []
    for turn in range(1, 7):
        if variant == "append-only":
            head = BASE
            turn_marker = f"\n\nTurn {turn} begins here."
        else:
            # same content, but a value that changes every turn is rendered at the TOP
            head = f"Turn {turn} begins here.\n\n" + BASE
            turn_marker = ""
        msgs = [{"role": "system", "content": "You are a terse infrastructure assistant."},
                {"role": "user", "content": head + turn_marker + f"\n\nReply with the single word T{turn}."}]
        ttft, _, pt, _ = call(msgs, max_tokens=6, tag=f"{variant} turn{turn}")
        hist.append(round(ttft, 3) if ttft else None)
    print(f"  {variant}: TTFT by turn = {hist}")