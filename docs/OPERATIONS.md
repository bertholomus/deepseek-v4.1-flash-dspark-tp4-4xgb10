# OPERATIONS — how this lane is run

Everything below is the *proven* discipline from 2026-09-11/12, including the two service
traps that cost real boots.

## 0. The only sanctioned entrypoints

| Action | Command |
|---|---|
| Boot / restart engine | `systemctl --user reset-failed deepseek-v41-flash-a1a4-sglang-tp4.service && systemctl --user restart deepseek-v41-flash-a1a4-sglang-tp4.service` |
| Adopt-or-start (script path) | `~/ai/runtime/deepseek-v41-flash-a1a4-sglang/adopt-or-start-tp4.sh` |
| Health | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health` → `200` |
| Watchdog state | `cat ~/ai/runtime/deepseek-v41-flash-a1a4-sglang/watchdog-state.json` |

### Trap 1 — `start` is a NO-OP after an out-of-band container stop
The unit is `Type=oneshot` + `RemainAfterExit=yes`. If the container dies (OOM, manual stop,
crash) the unit still reads `active (exited)`, so `systemctl --user start` silently does
nothing. **Always `restart`, never `start`.**

### Trap 2 — the start-rate limiter
After several restarts in a session, systemd refuses with
`start request repeated too quickly` / `start-limit-hit`. **Always `reset-failed` first.**
Every boot script in this repo bakes that in; if you write a new one, do the same.

## 1. Maintenance latch (mutual exclusion with the watchdog)

The watchdog restarts a sick lane — which is exactly what you don't want while you are
mid-experiment. So:

```bash
L=~/ai/runtime/deepseek-v41-flash-a1a4-sglang/maintenance.latch
touch $L                      # raise: watchdog parks itself
# ... planned work (boot, env change, profiling) ...
rm -f $L                      # ALWAYS drop it afterwards
```

**Never leave the latch up after planned work** — a latched lane has no health protection.
A boot script that raises the latch must drop it (or say loudly that it didn't). We shipped
this bug once: a profiling boot left the latch up and only a later audit caught it.

## 2. Warm-up is part of the lane, not an option

After every boot, run the warmer; it reaches plateau in ~128 s and is worth **+60–100% at
conc1** (cold 16.2 → warm 35–38).

```bash
~/ai/runtime/deepseek-v41-flash-a1a4-sglang/warm-lane.py
# expected tail: WARM_DONE / WARM_PLATEAU engine_tp=...
```

`adopt-or-start-tp4.sh` runs it automatically after a *fresh* boot (not after an adopt).

## 3. Keep-warm heartbeat (idle decay mitigation) — CURRENTLY PAUSED

`dsv41-keep-warm.timer` fires every 5 min; `keep-warm.py` runs the warmer **only if** the
lane has been idle ≥ 10 min (`KEEP_IDLE_S=600`) and respects the maintenance latch.

**Status 2026-09-12: paused (`inactive`), deliberately** — the idle-decay hypothesis it was
built for is unproven: single-stream probes on this lane are confounded by the fleet's live
traffic (see EVIDENCE §5). Do not enable it as a "win" until a contention-normalised
cold-vs-warm pair justifies it. Its safety logic is sound: "idle" is derived from the
engine's own `Decode batch` lines, which include **every** client, so it cannot fire while
the fleet is working.

```bash
systemctl --user enable --now dsv41-keep-warm.timer    # enable (when justified)
systemctl --user disable --now dsv41-keep-warm.timer   # rollback
```

Log: `~/ai/runtime/deepseek-v41-flash-a1a4-sglang/keep-warm.log`.
Measured: idle 341 s → burst → `WARM_PLATEAU engine_tp=108.99` in 92 s.

## 4. Changing the formula

```bash
cd ~/ai/recipes/DeepSeek-v4.1-Flash-DGX-Sparks
cp .env.tp4 .env.tp4.pre-<change>-$(date +%Y%m%d-%H%M%S)   # MANDATORY
# edit .env.tp4
systemctl --user reset-failed deepseek-v41-flash-a1a4-sglang-tp4.service
systemctl --user restart  deepseek-v41-flash-a1a4-sglang-tp4.service
```

Then **read the boot log for the resolved value** (do not assume the env line won) and run
`verify.sh`. Profiling-only knobs (`SGLANG_DSPARK_ENABLE_SPS_RECORD`,
`SGLANG_SIMULATE_ACC_LEN`) are dead ends on this build — see TUNING-LOG before re-trying.

## 5. Rollback chain

`.env.tp4.pre-gamma3-*` → gamma-4 · `.env.tp4.pre-gamma4-*` → gamma-5 (stock) ·
`.env.tp4.pre-pr7-*` → pre-tuning · `.env.tp4.pre-capaccept2-*` → pre-cap-accept trial.
Rolling back = copy the backup over `.env.tp4`, then restart (with `reset-failed`).
Keep the backups alongside the live file; they are the rollback, and they are proven.

## 6. Post-deploy verification

```bash
bash verify.sh            # health, model id/context, garbled gate, needle gate
```

Run it against a *quiet* lane, after warm-up; concurrency from other clients corrupts the
measurement. Watchdog timers stay enabled — they are read-only health checks, not traffic.