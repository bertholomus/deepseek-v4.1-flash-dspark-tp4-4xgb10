#!/usr/bin/env python3
"""Keep-warm heartbeat for the A1-A4 dsv41 TP4 lane (spark1).

Why (2026-09-12, partial decay measurement in progress — see handoff): single-stream reads
~45-49 tok/s right after lane warm, and a 300-tok probe 18 min into idle read 37.3 — decay
magnitude still being measured; no thermal component (clocks flat 2400 MHz idle). The proven
warm shape is warm-lane.py (concurrency 4-8 distinct prompts, ~90-130 s), which we reuse
here; whether tiny requests alone re-warm is unproven, so we do not rely on it.

Tick logic (systemd timer, every 5 min):
  skip if  lane not running / unhealthy
  skip if  maintenance.latch present (operator owns the lane right now)
  skip if  engine saw traffic < IDLE_THRESHOLD_S ago (backs off during real use)
  else     run warm-lane.py --budget-s 90 (proven warmer; exits at plateau or budget)

Worst-case idle gap with timer=300s / threshold=300s: ~8-10 min -> expect ~40+ tok/s
instead of ~20-28 cold. Rollback: systemctl --user disable --now dsv41-keep-warm.timer
"""
import os
import subprocess
import sys
import time

LANE_DIR = "/home/<user>/ai/runtime/deepseek-v41-flash-a1a4-sglang"
BASE = os.environ.get("KEEP_WARM_URL", "http://127.0.0.1:8000")
IDLE_THRESHOLD_S = int(os.environ.get("KEEP_IDLE_S", "300"))
BUDGET_S = int(os.environ.get("KEEP_BUDGET_S", "90"))


def sh(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def lane_running():
    out = sh(["docker", "inspect", "-f", "{{.State.Running}}", "dsv41-head"], 10).strip()
    if out != "true":
        return False
    import urllib.request
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def last_traffic_age_s():
    """Seconds since the last Decode-batch line (docker --timestamps = true UTC).
    Fail toward firing: unknown => treat as idle."""
    out = sh(["docker", "logs", "--timestamps", "--since", "6h", "dsv41-head"], 60)
    last = None
    for l in out.splitlines():
        if "Decode batch" in l:
            last = l.split()[0]
    if last is None:
        return 10**9
    import calendar
    t = calendar.timegm(time.strptime(last.split(".")[0].rstrip("Z"), "%Y-%m-%dT%H:%M:%S"))
    return max(0, int(time.time() - t))


def main():
    ts = time.strftime("%F %T")
    if not os.path.exists(os.path.join(LANE_DIR, "maintenance.latch")) and lane_running():
        age = last_traffic_age_s()
        if age < IDLE_THRESHOLD_S:
            print(f"{ts} idle age {age}s < {IDLE_THRESHOLD_S}s; no-op")
            return 0
        print(f"{ts} idle age {age}s >= {IDLE_THRESHOLD_S}s -> warm-lane burst")
        r = subprocess.run(
            [sys.executable, os.path.join(LANE_DIR, "warm-lane.py"),
             "--url", BASE, "--budget-s", str(BUDGET_S)],
            capture_output=True, text=True, timeout=420)
        tail = (r.stdout or "").strip().splitlines()[-3:]
        print(f"{ts} warm-lane rc={r.returncode}: {' | '.join(tail)}")
        return 0 if r.returncode == 0 else 1
    print(f"{ts} lane down or under maintenance; no-op")
    return 0


if __name__ == "__main__":
    sys.exit(main())
