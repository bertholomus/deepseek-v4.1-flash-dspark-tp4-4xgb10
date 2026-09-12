#!/usr/bin/env python3
"""Watchdog for the A1-A4 SGLang TP4 lane (DeepSeek-V4.1-Flash).

Discipline: exact container state on every node, host memory-pressure telemetry, a
semantic API canary, and a durable fail-closed latch after THRESHOLD consecutive
*functional* failures.

Two protections so planned work is not strangled:
  * Boot grace — while the head container is younger than BOOT_GRACE_S and the API is not
    up yet, the check reports `booting` and does NOT count failures (a cold TP4 load takes
    ~9 minutes; an in-flight boot is not an outage).
  * Maintenance latch — `maintenance.latch` present => the watchdog records and exits
    without counting or latching anything. Use it for any planned stop/serve.

Image checks run against a per-node manifest (`tp4-images.json`). Each node builds its own
image, so a single global digest is wrong; image drift is reported as `drifted` and does
NOT by itself stop a working engine.
"""
import json
import os
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/<user>/ai/runtime/deepseek-v41-flash-a1a4-sglang")
STATE = ROOT / "watchdog-state.json"
LATCH = ROOT / "restart-inhibit.latch"
MAINT = ROOT / "maintenance.latch"
IMAGES = ROOT / "tp4-images.json"
LEGACY_LATCH = Path("/home/<user>/ai/runtime/deepseek-v41-a1a4-candidate/restart-inhibit.latch")
LEGACY_SERVICE = "deepseek-v41-a1a4-tp4.service"
SERVICE = "deepseek-v41-flash-a1a4-sglang-tp4.service"
MODEL = "deepseek-v4.1-flash"
CONTEXT_LEN = 1048576
CANARY = "TP4_WATCHDOG_OK"
HEAD_CTN = "dsv41-head"
BOOT_GRACE_S = 1200
NODES = [("A1/head", None, HEAD_CTN), ("A2", "10.0.0.2", "dsv41-worker"),
         ("A3", "10.0.0.3", "dsv41-worker"), ("A4", "10.0.0.4", "dsv41-worker")]
THRESHOLD = 3
ABSOLUTE_CRITICAL_KIB = 1 * 1024 * 1024
LOW_MEMORY_KIB = 2 * 1024 * 1024
HIGH_PSI_AVG10 = 10.0


def run(cmd, timeout=45):
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"consecutive_failures": 0}


def load_images():
    try:
        return json.loads(IMAGES.read_text()).get("nodes", {})
    except Exception:
        return {}


def save(payload):
    payload["checked_at"] = datetime.now(timezone.utc).isoformat()
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, STATE)


def latch(errors):
    tmp = LATCH.with_suffix(".tmp")
    tmp.write_text(json.dumps({"latched_at": datetime.now(timezone.utc).isoformat(),
                               "reason": "watchdog-fail-closed", "errors": errors}, indent=2) + "\n")
    os.replace(tmp, LATCH)


def remote(host, command):
    if host is None:
        return run(["bash", "-lc", command])
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                f"bertholomus@{host}", command])


def head_age_seconds():
    p = remote(None, f"docker inspect {HEAD_CTN} --format='{{{{.State.StartedAt}}}}'")
    if p.returncode:
        return None
    try:
        started = datetime.fromisoformat(p.stdout.strip().replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - started).total_seconds()
    except ValueError:
        return None


def api_reachable():
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5).read()
        return True
    except Exception:
        return False


def node_check(label, host, ctn, expected_image):
    """Returns (functional_errors, drift_errors)."""
    command = (f"docker inspect {ctn} --format='{{{{.State.Running}}}}|"
               f"{{{{.State.OOMKilled}}}}|{{{{.State.Restarting}}}}|{{{{.Image}}}}'; "
               "m=$(awk '/MemAvailable:/{print $2}' /proc/meminfo); "
               "p=$(awk '/^some /{for(i=1;i<=NF;i++) if($i ~ /^avg10=/){split($i,a,\"=\"); print a[2]}}' /proc/pressure/memory); "
               "printf '%s %s\\n' \"$m\" \"$p\"")
    p = remote(host, command)
    if p.returncode:
        return [f"{label}: unreachable or container absent"], []
    lines = p.stdout.splitlines()
    if len(lines) != 2:
        return [f"{label}: malformed telemetry"], []
    functional, drift = [], []
    parts = lines[0].split("|")
    state, image = parts[:3], parts[3]
    if state != ["true", "false", "false"]:
        functional.append(f"{label}: container state {state}")
    if expected_image is None:
        drift.append(f"{label}: no image recorded in manifest")
    elif image != expected_image:
        drift.append(f"{label}: image drift {image} != manifest {expected_image}")
    try:
        available_text, psi_text = lines[1].split()
        available = int(available_text)
        psi_avg10 = float(psi_text)
        if available < ABSOLUTE_CRITICAL_KIB:
            functional.append(f"{label}: MemAvailable critical at {available} KiB")
        elif available < LOW_MEMORY_KIB and psi_avg10 > HIGH_PSI_AVG10:
            functional.append(f"{label}: sustained memory pressure at {available} KiB, PSI avg10={psi_avg10}")
    except ValueError:
        functional.append(f"{label}: invalid memory telemetry")
    return functional, drift


def api_check():
    with urllib.request.urlopen("http://127.0.0.1:8000/v1/models", timeout=10) as r:
        models = json.load(r)
    rows = [x for x in models.get("data", []) if x.get("id") == MODEL]
    if len(rows) != 1 or rows[0].get("max_model_len") != CONTEXT_LEN:
        raise RuntimeError("model identity/context drift")
    with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10) as r:
        if r.status != 200:
            raise RuntimeError(f"health status {r.status}")
    payload = json.dumps({"model": MODEL,
                          "messages": [{"role": "user", "content": f"Reply exactly {CANARY}"}],
                          "temperature": 0, "max_tokens": 24,
                          "chat_template_kwargs": {"thinking": False}}).encode()
    req = urllib.request.Request("http://127.0.0.1:8000/v1/chat/completions", data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.load(r)
    text = (body["choices"][0]["message"].get("content") or "").strip()
    if text != CANARY:
        raise RuntimeError(f"unexpected semantic response {text!r}")
    return {"canary": text}


def main():
    state = load_state()
    active = run(["systemctl", "--user", "is-active", "--quiet", SERVICE]).returncode == 0

    if MAINT.exists() and not LATCH.exists():
        save({"consecutive_failures": int(state.get("consecutive_failures", 0)),
              "latched": False, "status": "maintenance"})
        print("TP4_WATCHDOG_MAINTENANCE")
        return

    if LATCH.exists():
        if active:
            run(["systemctl", "--user", "stop", SERVICE], timeout=650)
        save({"consecutive_failures": int(state.get("consecutive_failures", 0)),
              "latched": True, "status": "restart-inhibited"})
        raise SystemExit(f"durable latch present: {LATCH}")

    if not LEGACY_LATCH.exists() and run(["systemctl", "--user", "is-active", "--quiet",
                                         LEGACY_SERVICE]).returncode == 0:
        errors = [f"ownership conflict: {LEGACY_SERVICE} is unlatched and active"]
        latch(errors)
        save({"consecutive_failures": 0, "latched": True, "status": "ownership-conflict", "errors": errors})
        raise SystemExit("TP4 watchdog: ownership conflict, latched")

    if not active:
        save({"consecutive_failures": 0, "latched": False, "status": "inactive-noop"})
        print("TP4_WATCHDOG_INACTIVE_NOOP")
        return

    images = load_images()
    functional, drift = [], []
    for label, host, ctn in NODES:
        f, d = node_check(label, host, ctn, images.get(label))
        functional.extend(f)
        drift.extend(d)

    api_error = None
    try:
        api_check()
    except Exception as exc:
        api_error = f"api: {type(exc).__name__}: {exc}"

    if api_error and not functional:
        # Containers are all up but the API is not answering. During a cold load that is
        # normal for ~9 minutes; do not strangle a boot in progress.
        age = head_age_seconds()
        if age is not None and age < BOOT_GRACE_S and not api_reachable():
            save({"consecutive_failures": int(state.get("consecutive_failures", 0)),
                  "latched": False, "status": "booting", "head_age_s": int(age)})
            print(f"TP4_WATCHDOG_BOOTING head_age={int(age)}s")
            return
        functional.append(api_error)
    elif api_error:
        functional.append(api_error)

    if not functional and not drift:
        save({"consecutive_failures": 0, "latched": False, "status": "healthy"})
        print("TP4_WATCHDOG_HEALTHY")
        return

    if not functional and drift:
        save({"consecutive_failures": 0, "latched": False, "status": "drifted", "drift": drift})
        print("TP4_WATCHDOG_DRIFTED")
        raise SystemExit("TP4 watchdog drift (engine healthy, not stopped): " + "; ".join(drift))

    failures = int(state.get("consecutive_failures", 0)) + 1
    payload = {"consecutive_failures": failures, "latched": False,
               "status": "unhealthy", "errors": functional, "drift": drift}
    if failures >= THRESHOLD:
        latch(functional + drift)
        stopped = run(["systemctl", "--user", "stop", SERVICE], timeout=650)
        payload.update({"latched": True, "status": "stopped-fail-closed",
                        "stop_returncode": stopped.returncode})
    save(payload)
    raise SystemExit("TP4 watchdog failure: " + "; ".join(functional))


if __name__ == "__main__":
    main()