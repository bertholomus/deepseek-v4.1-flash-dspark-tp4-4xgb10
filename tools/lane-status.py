#!/usr/bin/env python3
"""One-shot status for the A1-A4 dsv41 TP4 lane, contention-aware.

Answers the question every agent asks: "is it slow, or is it busy?"
Read-only: reads /health and the head container's last decode lines.

Usage (spark1 or any host that can reach the lane + has docker on the head):
    python3 lane-status.py
"""
import calendar, json, re, subprocess, sys, time, urllib.request

BASE = "http://100.64.0.1:8000"
CONT = "dsv41-head"
TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
DEC = re.compile(r"Decode batch, #running-req: (\d+), #full token: (\d+).*?accept len: ([0-9.]+), "
                 r"accept rate: ([0-9.]+).*?gen throughput \(token/s\): ([0-9.]+), #queue-req: (\d+)")

# health + identity
try:
    h = urllib.request.urlopen(BASE + "/health", timeout=10).status
except Exception as e:
    h = f"UNREACHABLE ({e})"
try:
    ident = json.load(urllib.request.urlopen(BASE + "/v1/models", timeout=10))["data"][0]
    mid, mlen = ident["id"], ident.get("max_model_len")
except Exception as e:
    mid, mlen = f"?({e})", "?"

print(f"lane      : {BASE}  health={h}  model={mid}  context={mlen}")

# last decode lines
out = subprocess.run(["docker", "logs", "--tail", "400", CONT],
                     capture_output=True, text=True, timeout=60).stdout
lines = []
for ln in out.splitlines():
    m = TS.match(ln)
    d = DEC.search(ln)
    if m and d:
        lines.append((calendar.timegm(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")),
                      int(d.group(1)), int(d.group(2)), float(d.group(3)),
                      float(d.group(4)), float(d.group(5)), int(d.group(6))))
if not lines:
    print("no recent decode activity (lane idle or just booted)")
    sys.exit(0)

now = time.time()
last = lines[-1]
age = now - last[0]
window = [x for x in lines if now - x[0] <= 120]

rq_med = sorted(x[1] for x in window)[len(window) // 2] if window else last[1]
agg_med = sorted(x[5] for x in window)[len(window) // 2] if window else last[5]
per_stream = agg_med / max(rq_med, 1)

print(f"last step : {age:5.1f}s ago   running-req={last[1]}  queue={last[6]}  "
      f"accept len={last[3]:.2f} (rate {last[4]:.2f})  aggregate={last[5]:.1f} tok/s")
print(f"last 2 min: median running-req={rq_med}  median aggregate={agg_med:.1f} tok/s "
      f"-> ~{per_stream:.1f} tok/s per active stream")
if age > 60:
    verdict = "IDLE (nothing decoding right now)"
elif rq_med <= 1:
    verdict = "QUIET / single stream (this is the number to quote as single-stream)"
elif rq_med <= 3:
    verdict = "LIGHT fleet load (expect ~half of quiet single-stream per client)"
else:
    verdict = f"BUSY fleet load ({rq_med} streams): per-stream speed is shared, not broken"
print(f"verdict   : {verdict}")
print("reminder  : this lane serves the fleet (our other agent lanes). "
      "Quote single-stream numbers only when the verdict is QUIET.")