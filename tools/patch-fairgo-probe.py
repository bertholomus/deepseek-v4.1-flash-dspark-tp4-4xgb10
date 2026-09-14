#!/usr/bin/env python3
"""Create $RT/fairgo-probe.py from gamma-ctx-probe.py by adding a --salt option.

The salt is prepended to the filler context so every window arm primes its OWN prefix:
the packed/engram prefix cache survives engine reboots, so without a salt a later arm
silently inherits warm prefixes from an earlier one and arm order leaks into the numbers.
Fails loudly if either anchor is missing (probe source changed under us).
"""
import os
import sys

RT = os.path.expanduser("~/ai/runtime/deepseek-v41-flash-a1a4-sglang")
src_path = os.path.join(RT, "gamma-ctx-probe.py")
dst_path = os.path.join(RT, "fairgo-probe.py")

src = open(src_path).read()

a = src.replace(
    '"--sizes", default="8000,32000,96000")',
    '"--sizes", default="8000,32000,96000")\n    ap.add_argument("--salt", default="")',
    1,
)
assert a != src, "argparse anchor not found — probe source changed"

b = a.replace(
    "            ctx = FILLER * max(1, s // 31)",
    '            salt = f"[run {args.salt}] " if args.salt else ""\n'
    "            ctx = salt + FILLER * max(1, s // 31)",
    1,
)
assert b != a, "ctx anchor not found — probe source changed"

open(dst_path, "w").write(b)
print(f"patched {dst_path} ({len(b)} bytes)")
assert "--salt" in b and "salt = f" in b
sys.exit(0)