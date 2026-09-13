#!/usr/bin/env python3
"""Classify every prefill the lane actually served: how much of it was COLD (uncached)?

Reads the engine's own `Prefill batch` lines. Two views, because chunked prefill splits one
request into chunk-sized batches:
  * per BATCH  - what the engine scheduled;
  * per REQUEST - batches clustered by wall-clock proximity (CLUSTER_S), which reconstructs the
    real per-turn cold cost. A healthy append-only turn is small; a re-prefill is the whole tail.

Usage: python3 classify-prefills.py <logfile>
"""
import calendar, re, statistics, sys, time as _t

PREFILL_TOK_S = 3.7e3     # measured: tools/ttft-probe.py
CHUNK = 4096              # chunked_prefill_size
CLUSTER_S = 2.5

TS = re.compile(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) T")
NEW = re.compile(r"#new-token: (\d+), #cached-token: (\d+)")
RUN = re.compile(r"#running-req: (\d+), #queue-req: (\d+)")

rows = []
for line in open(sys.argv[1], encoding="utf-8", errors="ignore"):
    a, b, c = TS.search(line), NEW.search(line), RUN.search(line)
    if a and b:
        ts = calendar.timegm(_t.strptime(a.group(1), "%Y-%m-%d %H:%M:%S"))
        rows.append((ts, int(b.group(1)), int(b.group(2)),
                     int(c.group(2)) if c else -1))
if not rows:
    print("no Prefill batch lines found")
    sys.exit(0)

clusters, cur = [], [rows[0]]
for r in rows[1:]:
    if r[0] - cur[-1][0] <= CLUSTER_S:
        cur.append(r)
    else:
        clusters.append(cur)
        cur = [r]
clusters.append(cur)
span_s = rows[-1][0] - rows[0][0] + 1
req_new = [sum(c[1] for c in cl) for cl in clusters]
batch_new = [r[1] for r in rows]
total_new = sum(batch_new)

print(f"window span              : {span_s}s, {len(rows)} prefill batches "
      f"→ {len(clusters)} requests (clustered at {CLUSTER_S}s)")
print(f"uncached tokens total    : {total_new:,} of {total_new + sum(r[2] for r in rows):,} "
      f"prompt tokens")
print(f"cold prefill time        : {total_new/PREFILL_TOK_S:.0f}s at the measured "
      f"{PREFILL_TOK_S/1000:.1f}k tok/s  = {100*total_new/PREFILL_TOK_S/span_s:.1f} % of the window")
print(f"per BATCH uncached       : p50 {statistics.median(batch_new):,.0f}  max {max(batch_new):,}"
      f"   (full {CHUNK}-token chunks: {sum(1 for n in batch_new if n == CHUNK)})")
print(f"per REQUEST uncached     : p50 {statistics.median(req_new):,.0f}  "
      f"p90 {sorted(req_new)[int(0.9*len(req_new))-1]:,}  max {max(req_new):,}")

for label, sel in (("≤1k  (a healthy appended turn)", [n for n in req_new if n <= 1024]),
                   ("1k–8k (a tool output / document)", [n for n in req_new if 1024 < n <= 8192]),
                   (">8k  (a re-prefill candidate)", [n for n in req_new if n > 8192])):
    if sel:
        print(f"  {label:<34} {len(sel):4d} requests {100*len(sel)/len(req_new):5.1f} %  "
              f"uncached {sum(sel):>9,} tok ≈{sum(sel)/PREFILL_TOK_S:6.1f}s")

big = sorted(clusters, key=lambda cl: -sum(c[1] for c in cl))[:6]
print("\nlargest per-request cold prefills:")
for cl in big:
    n, c = sum(x[1] for x in cl), sum(x[2] for x in cl)
    print(f"  {n:>7,} uncached / {c:>8,} cached ({len(cl)} chunks)  "
          f"≈{n/PREFILL_TOK_S:5.1f}s cold prefill, cached share {100*c/(c+n):.1f} %, "
          f"queue-req seen {max(x[3] for x in cl)}")