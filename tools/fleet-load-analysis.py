#!/usr/bin/env python3
"""Read-only analysis of the live A1-A4 engine log: what the FLEET actually experiences.

No traffic generated. Parses dsv41-head container logs into:
  - per-stream decode speed vs #running-req (the fleet's real felt speed)
  - prefill batches: size, cached-token share (radix cache effectiveness)
  - queue depth over time (starvation indicator)
  - accept length/rate at the fast path
  - step interval (ms) distribution
Usage: python3 fleet-load-analysis.py [tail_lines]
"""
import calendar, re, subprocess, sys, time, statistics as st
from collections import defaultdict, Counter

TAIL = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
DEC = re.compile(r"Decode batch, #running-req: (\d+), #full token: (\d+).*?accept len: ([0-9.]+), "
                 r"accept rate: ([0-9.]+).*?gen throughput \(token/s\): ([0-9.]+), #queue-req: (\d+)")
PRE = re.compile(r"Prefill batch, #new-seq: (\d+), #new-token: (\d+), #cached-token: (\d+).*?"
                 r"#running-req: (\d+), #queue-req: (\d+), #pending-token: (\d+).*?"
                 r"input throughput \(token/s\): ([0-9.]+)")

out = subprocess.run(["docker", "logs", "--tail", str(TAIL), "dsv41-head"],
                     capture_output=True, text=True, timeout=120).stdout

dec, pre = [], []          # (epoch, ...)
for ln in out.splitlines():
    m = TS.match(ln)
    if not m:
        continue
    t = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))  # container logs UTC
    d = DEC.search(ln)
    if d:
        dec.append((t, int(d.group(1)), int(d.group(2)), float(d.group(3)), float(d.group(4)), float(d.group(5)), int(d.group(6))))
        continue
    p = PRE.search(ln)
    if p:
        pre.append((t,) + tuple(int(x) if i != 6 else float(x) for i, x in enumerate(p.groups())))

print(f"parsed: {len(dec)} decode steps, {len(pre)} prefill batches (from {TAIL} log lines)")

if dec:
    span = dec[-1][0] - dec[0][0]
    print(f"window: {span/60:.1f} min  ({span/max(len(dec),1)*1000:.0f} ms/step avg incl. idle)")

    # per-stream speed by concurrency
    byc = defaultdict(list)
    for _, rq, _, _, _, tp, _ in dec:
        byc[rq].append(tp / max(rq, 1))
    print("\nper-stream decode tok/s by #running-req (the felt speed):")
    for rq in sorted(byc):
        v = sorted(byc[rq])
        print(f"  req={rq:2d}  n={len(v):4d}  median {v[len(v)//2]:6.1f}  p10 {v[len(v)//10]:6.1f}  max {v[-1]:6.1f}")

    agg = [tp for _, _, _, _, _, tp, _ in dec]
    print(f"\naggregate gen throughput: median {st.median(agg):.1f}  max {max(agg):.1f} tok/s")
    acl = [a for _, _, _, a, _, _, _ in dec]
    acr = [r for _, _, _, _, r, _, _ in dec]
    print(f"accept len: median {st.median(acl):.2f} (min {min(acl):.2f} max {max(acl):.2f}) | "
          f"accept rate: median {st.median(acr):.2f}")
    q = Counter(x[6] for x in dec)
    tot_q = sum(k * v for k, v in q.items())
    print(f"queue depth: {dict(sorted(q.items()))} -> mean {tot_q/len(dec):.2f} queued req/step")
    full = [f for _, _, f, _, _, _, _ in dec]
    print(f"context in flight: median {st.median(full):,} tok  max {max(full):,}")

if pre:
    ns  = [p[1] for p in pre]; nt = [p[2] for p in pre]; ct = [p[3] for p in pre]
    itp = [p[7] for p in pre]; pend = [p[6] for p in pre]
    tot_new = sum(nt); tot_cached = sum(ct)
    print(f"\nprefill batches: {len(pre)} | new-seq median {st.median(ns):.0f} | "
          f"new-token median {st.median(nt):,.0f} | pending-token median {st.median(pend):,.0f}")
    print(f"radix cache: {tot_cached:,} cached of {tot_cached+tot_new:,} prompt tokens "
          f"= {100*tot_cached/max(tot_cached+tot_new,1):.1f}% hit")
    print(f"prefill input throughput: median {st.median(itp):,.1f} tok/s  max {max(itp):,.1f}")

# interleaving: how often a prefill lands between decode steps (stalls decode work)
if dec and pre:
    td = [x[0] for x in dec]
    gaps = [(td[i+1]-td[i]) for i in range(len(td)-1) if 0 < td[i+1]-td[i] < 30]
    pgaps = [t for t in (p[0] for p in pre)]
    near = sum(1 for t in pgaps if any(0 <= t - d <= 1 for d in td))
    print(f"\nprefill/decode interleaving: {near}/{len(pre)} prefill batches land within 1 s of a decode step")
    if gaps:
        print(f"decode step interval: median {st.median(gaps)*1000:.0f} ms  p90 {sorted(gaps)[int(len(gaps)*0.9)]*1000:.0f} ms")