#!/usr/bin/env python3
"""Read-only: why is prefill slow under fleet load? (no traffic generated)

Splits prefill batches by size and by what else was running, and reports the implied
prefill wall time (new-token / input-throughput), so we can see whether the cost is
per-batch fixed overhead, competing decode, or cache behaviour.
Usage: python3 prefill-deepdive.py [tail_lines]
"""
import calendar, re, subprocess, sys, time, statistics as st
from collections import defaultdict

TAIL = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
PRE = re.compile(r"Prefill batch, #new-seq: (\d+), #new-token: (\d+), #cached-token: (\d+).*?"
                 r"#running-req: (\d+), #queue-req: (\d+), #pending-token: (\d+).*?"
                 r"input throughput \(token/s\): ([0-9.]+)")
DEC = re.compile(r"Decode batch, #running-req: (\d+)")

out = subprocess.run(["docker", "logs", "--tail", str(TAIL), "dsv41-head"],
                     capture_output=True, text=True, timeout=180).stdout

dec_t = []
rows = []
for ln in out.splitlines():
    m = TS.match(ln)
    if not m:
        continue
    t = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    if DEC.search(ln):
        dec_t.append(t)
    p = PRE.search(ln)
    if p:
        ns, nt, ct, rq, qr, pend, itp = p.groups()
        rows.append(dict(t=t, ns=int(ns), nt=int(nt), ct=int(ct), rq=int(rq),
                         qr=int(qr), pend=int(pend), itp=float(itp)))

print(f"prefill batches parsed: {len(rows)}   (decode lines for context: {len(dec_t)})")
if not rows:
    sys.exit(0)

# bucket by new-token size
def bucket(n):
    for lim, name in ((64, "<=64"), (256, "65-256"), (1024, "257-1k"), (4096, "1k-4k"), (16384, "4k-16k")):
        if n <= lim:
            return name
    return ">16k"

b = defaultdict(list)
for r in rows:
    b[bucket(r["nt"])].append(r)

print("\nby new-token size  (implied wall = new_token / input_throughput):")
print(f"  {'bucket':>8} {'n':>5} {'newtok med':>10} {'in-tp med':>10} {'in-tp p90':>10} {'wall med':>9} {'cached%':>8}")
for k in ["<=64", "65-256", "257-1k", "1k-4k", "4k-16k", ">16k"]:
    v = b.get(k)
    if not v:
        continue
    nt = sorted(x["nt"] for x in v)
    itp = sorted(x["itp"] for x in v)
    wall = sorted(x["nt"] / max(x["itp"], 1e-9) for x in v)
    tot_c = sum(x["ct"] for x in v); tot_n = sum(x["nt"] for x in v)
    print(f"  {k:>8} {len(v):>5} {nt[len(nt)//2]:>10,} {itp[len(itp)//2]:>10.0f} "
          f"{itp[int(len(itp)*0.9)]:>10.0f} {wall[len(wall)//2]:>8.2f}s {100*tot_c/max(tot_c+tot_n,1):>7.1f}%")

# uncached, mid-size prefills: the real work
work = [r for r in rows if r["ct"] == 0 and r["nt"] >= 256]
print(f"\nfully-uncached prefills >=256 tok: {len(work)}")
if work:
    itp = sorted(x["itp"] for x in work)
    print(f"  input throughput: median {itp[len(itp)//2]:.0f}  p10 {itp[len(itp)//10]:.0f}  p90 {itp[int(len(itp)*0.9)]:.0f}  max {itp[-1]:.0f} tok/s")
    print(f"  implied prefill wall: median {st.median([x['nt']/x['itp'] for x in work]):.2f}s")

# does concurrent decode depress prefill?
print("\nprefill input-throughput by concurrent decode load (uncached, >=256 tok):")
by_rq = defaultdict(list)
for r in work:
    by_rq[r["rq"]].append(r["itp"])
for rq in sorted(by_rq):
    v = sorted(by_rq[rq])
    print(f"  running-req={rq}: n={len(v):4d}  median {v[len(v)//2]:7.0f} tok/s")

# was a decode step logged within 1s either side? (rough competition proxy)
near = 0
for r in work:
    if any(abs(t - r["t"]) <= 1 for t in dec_t):
        near += 1
print(f"\n  {near}/{len(work)} uncached prefills had a decode log-line within 1 s "
      f"(proxy for competing decode traffic)")