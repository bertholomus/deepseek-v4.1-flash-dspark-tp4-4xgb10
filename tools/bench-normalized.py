#!/usr/bin/env python3
"""Contention-normalized single-stream bench for the A1-A4 dsv41 TP4 lane.

Why this exists: the lane serves the fleet (our other agent lanes profiles all
target http://100.64.0.1:8000/v1). A plain client tok/s number is therefore
meaningless unless you also record what else was running. This tool records both.

For each of N single-stream requests it reports:
  - client tok/s           (wall-clock, what a user feels)
  - engine gen-throughput  (decode lines that landed during the request window)
  - max #running-req seen  (0 = lane was otherwise idle)
and prints a verdict line: CLEAN (no other traffic) or CONTENDED (normalize before use).

Usage (on a host with docker access to the head, e.g. spark1):
    python3 bench-normalized.py [n_requests] [max_tokens]
"""
import json, re, subprocess, sys, time, urllib.request, calendar

BASE = "http://100.64.0.1:8000"
PROMPT = ("Describe in detail, as a numbered list, how a citywide district-heating network "
          "moves heat from a source plant to a building: pump stations, heat exchangers, "
          "return temperatures, and why oversizing pumps wastes energy. Be thorough.")
DECODE_RE = re.compile(r"#running-req:\s*(\d+).*?gen throughput \(token/s\):\s*([0-9.]+)")
TS_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def decode_lines(since_epoch):
    """Decode-batch lines emitted after `since_epoch` (head container only)."""
    out = subprocess.run(["docker", "logs", "--tail", "1500", "dsv41-head"],
                         capture_output=True, text=True, timeout=60).stdout
    keep = []
    for ln in out.splitlines():
        m = TS_RE.match(ln)
        if not m or "Decode batch" not in ln:
            continue
        t = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))  # container logs UTC
        if t >= since_epoch:
            d = DECODE_RE.search(ln)
            if d:
                keep.append((int(d.group(1)), float(d.group(2))))
    return keep


def one(max_tokens):
    body = json.dumps({"model": "deepseek-v4.1-flash",
                       "messages": [{"role": "user", "content": PROMPT}],
                       "max_tokens": max_tokens, "temperature": 0.0, "stream": False}).encode()
    t0 = time.time()
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        BASE + "/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"}), timeout=300))
    dt = time.time() - t0
    ct = (r.get("usage") or {}).get("completion_tokens") or 0
    return ct, dt, t0


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    mt = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    print(f"=== contention-normalized single-stream bench: {n} requests, {mt} tok ===")
    rows = []
    for i in range(1, n + 1):
        ct, dt, t0 = one(mt)
        lines = decode_lines(t0 - 2)
        other = [x for x in lines if x[0] > 1]
        solo = [x for x in lines if x[0] == 1]
        maxreq = max([x[0] for x in lines], default=0)
        verdict = "CLEAN" if not other else f"CONTENDED(max running-req {maxreq})"
        eng = f"engine@req1 median {sorted(x[1] for x in solo)[len(solo)//2]:.1f} tok/s" if solo else "engine@req1: none"
        print(f"  #{i}: client {ct/dt:6.1f} tok/s ({ct} tok in {dt:.1f}s) | {eng} | {verdict}")
        rows.append((ct / dt, maxreq, verdict))
    clean = [r for r in rows if r[2] == "CLEAN"]
    if clean:
        vals = sorted(r[0] for r in clean)
        print(f"  ==> CLEAN-STREAM median {vals[len(vals)//2]:.1f} tok/s over {len(clean)} clean request(s)")
    else:
        print("  ==> no clean request this run: all samples contended, do not compare against "
              "single-tenant numbers without saying so")
    print(f"  (clean {len(clean)}/{len(rows)})")


if __name__ == "__main__":
    main()