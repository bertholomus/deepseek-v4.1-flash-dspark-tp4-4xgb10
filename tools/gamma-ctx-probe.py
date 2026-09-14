#!/usr/bin/env python3
"""Gamma (DSpark draft depth) probe at fleet-realistic long context.

Why this exists
--------------
The 2026-09-12 gamma sweep (5 -> 4 -> 3, docs/TUNING-LOG.md section 1) closed on SHORT synthetic
prompts, when the lane was chat-shaped. Public peer data (joesinvestments RECIPE.md, 4x DGX Spark,
V4-Flash) swept the SAME knob at ~105k tokens of real context and found the opposite ordering:
k=3 40.6 tok/s, k=5 49.3, k=7 68.6 -- deeper drafts accept far better once the context is deep
and cached. Our fleet now runs exactly that shape (Hermes sessions, p50 prompt 52k, p90 148k).
So the closed sweep has to be re-opened for long context, with the same protocol on every arm.

Protocol
--------
- Prefix is PRIMED per size first (one short call), so measured calls are the real agentic case:
  stable cached context + new tokens. This is a decode measurement, not a prefill one.
- The three sizes are INTERLEAVED rep by rep so fleet contention hits every arm equally
  (same design as tools/context-curve-interleaved.py).
- Two output styles per size: prose and code (the two dominant fleet shapes); a list/counting
  style is kept as a secondary, because it flatters speculative decoding and would bias the call.
- 300 generated tokens per call so decode rate is not a small-sample artifact.

Per-call timeout is 600 s: a hung request is recorded as a failure and the arm is marked suspect
rather than left to wedge the window (see the 2026-09-14 long-context decode hang,
sgl-project/sglang#33549 - our own 128k cold probe tripped it on the live lane).

Usage: python3 gamma-ctx-probe.py --label gamma7 --out /path/arm.json [--sizes 8000,32000,96000]
Exit: 0 = clean, 3 = at least one timed-out/failed call (arm is suspect).
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8000/v1/chat/completions"
MODEL = "deepseek-v4.1-flash"
FILLER = ("Operational note for the record: the rack stays at twenty-one degrees, the switch "
          "uplinks are aggregated, and the spare SFP is in drawer three. ")

PROMPTS = {
    "prose": ("Write 300 words of plain narration about a maintenance shift on a server floor. "
              "No lists, no headings, continuous prose."),
    "code": ("Write a Python module of about 300 tokens implementing a bounded retry decorator "
             "with jittered backoff, a ring-buffer logger, and a small argparse CLI. "
             "Output code only."),
    "list": ("Write 300 short status lines, one per line, each a word and a two digit number."),
}
# list/counting output is reported but never decides the arm
PRIMARY_STYLES = ("prose", "code")
GEN = 300
REPS = 3
FAILURES = []


def call(messages, max_tokens, tag):
    body = json.dumps({"model": MODEL, "messages": messages, "max_tokens": max_tokens,
                       "temperature": 0.0, "stream": True}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    ttft, n, first, last = None, 0, None, None
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
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
    except Exception as e:  # timeout / disconnect / engine hang
        print(f"  {tag:<22} FAILED after {time.time() - t0:6.1f}s: {type(e).__name__}: {e}",
              flush=True)
        FAILURES.append(tag)
        return None
    rate = n / (last - first) if (n and last and last > first) else 0.0
    print(f"  {tag:<22} ttft={ttft:6.3f}s  decode={rate:6.1f} tok/s  (n={n})", flush=True)
    return {"ttft": ttft, "decode_tok_s": rate, "n": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sizes", default="8000,32000,96000")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]

    msgs = {}
    for s in sizes:
        for style, instruction in PROMPTS.items():
            ctx = FILLER * max(1, s // 28)
            base = [{"role": "system", "content": "You are a terse infrastructure assistant."},
                    {"role": "user", "content": ctx + "\n\nReply with the word READY."}]
            msgs[(s, style)] = base + [{"role": "assistant", "content": "READY"},
                                       {"role": "user", "content": instruction}]

    print(f"=== {args.label}: priming prefixes (cold fills) ===", flush=True)
    for s in sizes:
        call(msgs[(s, "prose")][:2], 4, f"{s // 1000}k prime")

    print(f"\n=== {args.label}: interleaved reps (contention hits every arm equally) ===",
          flush=True)
    res = {(s, style): [] for s in sizes for style in PROMPTS}
    for rep in range(1, REPS + 1):
        for s in sizes:
            for style in PROMPTS:
                r = call(msgs[(s, style)], GEN, f"{s // 1000}k {style} r{rep}")
                if r:
                    res[(s, style)].append(r["decode_tok_s"])

    summary = {}
    print(f"\n--- {args.label}: median decode tok/s ---", flush=True)
    for s in sizes:
        row = {}
        for style in PROMPTS:
            vals = res[(s, style)]
            row[style] = round(statistics.median(vals), 2) if vals else None
        prim = [row[st] for st in PRIMARY_STYLES if row[st] is not None]
        row["primary"] = round(statistics.mean(prim), 2) if prim else None
        summary[f"{s // 1000}k"] = row
        print(f"  {s // 1000:>3}k ctx : prose={row['prose']}  code={row['code']}  "
              f"list={row['list']}  PRIMARY={row['primary']}", flush=True)

    with open(args.out, "w") as f:
        json.dump({"label": args.label, "sizes": sizes, "reps": REPS, "gen": GEN,
                   "summary": summary, "raw": {f"{k[0] // 1000}k/{k[1]}": v for k, v in res.items()},
                   "failures": FAILURES}, f, indent=2)
    print(f"\nwrote {args.out}  failures={len(FAILURES)}", flush=True)
    sys.exit(3 if FAILURES else 0)


if __name__ == "__main__":
    main()