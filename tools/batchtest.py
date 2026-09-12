#!/usr/bin/env python3
"""Garbled-output gate: deterministic prompts, temp 0, token-sanity checks."""
import json, re, urllib.request

URL = "http://127.0.0.1:8000/v1/chat/completions"
PROMPTS = [
    "Reply with exactly: PELICAN-1 GATE OK",
    "Count from 1 to 20, comma-separated, no other text.",
    "Name the first 8 elements of the periodic table in order.",
    "Write the word 'harbor' backwards, then forwards.",
    "Output the alphabet from A to J as capital letters joined by hyphens.",
]
def ask(p):
    body = json.dumps({"model": "deepseek-v4.1-flash",
                       "messages": [{"role": "user", "content": p}],
                       "temperature": 0, "max_tokens": 200}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)["choices"][0]["message"]["content"]

# (pattern, extra-literal-or-None, is_regex)
EXPECT = [
    ("PELICAN-1", "GATE OK", False),
    (r"\b1\b.*\b20\b", None, True),
    ("Hydrogen", "Helium", False),
    ("robrah", "harbor", False),
    ("A-B-C-D-E-F-G-H-I-J", None, False),
]
garbled = 0
for i, (p, (a, b, is_rx)) in enumerate(zip(PROMPTS, EXPECT)):
    out = ask(p)
    if is_rx:
        ok = re.search(a, out, re.S) is not None and (b is None or b.lower() in out.lower())
    else:
        ok = a.lower() in out.lower() and (b is None or b.lower() in out.lower())
    print(f"  gate#{i+1}: {'PASS' if ok else 'FAIL'} :: {out[:80]!r}")
    if not ok: garbled += 1
print(f"GATE: garbled={garbled}")
raise SystemExit(1 if garbled else 0)
