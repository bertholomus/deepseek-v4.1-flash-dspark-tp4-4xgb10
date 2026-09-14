#!/usr/bin/env bash
# DSV4.1 TP4 — GAMMA (DSpark draft depth) window at fleet-realistic long context. UNATTENDED.
#
# Arms: control (live DSPARK_BLOCK_SIZE, asserted = 3) -> 5 -> 7, each with its own boot.
# Probe: tools/gamma-ctx-probe.py (8k / 32k / 96k interleaved, prose + code primary).
# Decision rule (banked here so the window cannot be re-interpreted after the fact):
#   adopt the arm with the highest PRIMARY median at 96k IF it beats control by >= 8%
#   AND beats control by >= 3% at 32k; otherwise keep the control value. Ties/inside-noise
#   => keep control (same conservative stance as the 2026-09-14 prefill-chunk window).
# The window always ends on a VERIFIED healthy lane, latches dropped, env md5 recorded.
#
# Read-only outside .env.tp4 (one key, backed up per arm) + one service restart per arm.
# Every measured arm is preceded by warm + garbled-output gate. A probe that reports failures
# (hung request, see sgl-project/sglang#33549) aborts the arm and the window reverts to control.
#
# Usage on spark1:  bash window-gamma-longctx.sh
set -uo pipefail

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
R="$HOME/ai/recipes/DeepSeek-v4.1-Flash-DGX-Sparks"
RT="$HOME/ai/runtime/deepseek-v41-flash-a1a4-sglang"
U="deepseek-v41-flash-a1a4-sglang-tp4.service"
L="$RT/maintenance.latch"
W="$HOME/dsv41-state/window-gamma-$STAMP"
ENVF="$R/.env.tp4"
PROBE="$RT/gamma-ctx-probe.py"
mkdir -p "$W/results"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$W/runner.log"; }
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 6 http://127.0.0.1:8000/health || echo ERR; }
cleanup() { rm -f "$L"; log "latch dropped (trap)"; }
trap cleanup EXIT

log "=== gamma window start $STAMP ==="
log "env md5 before: $(md5sum "$ENVF" | cut -d' ' -f1)"
cp -a "$ENVF" "$W/.env.tp4.orig"
grep -n '^DSPARK_BLOCK_SIZE=' "$ENVF" | tee -a "$W/runner.log"

if [ "$(health)" != "200" ]; then
  log "ABORT: lane not healthy at window start (health=$(health))"
  echo "aborted: starting health was $(health)" > "$W/SUMMARY.txt"
  exit 1
fi
# Stage the probe + discriminator onto the runtime dir (they may arrive in /tmp/window/).
for cand in "$RT/gamma-ctx-probe.py" "$R/tools/gamma-ctx-probe.py" /tmp/window/gamma-ctx-probe.py; do
  [ -f "$cand" ] && { cp -f "$cand" "$RT/gamma-ctx-probe.py"; break; }
done
[ -f "$RT/gpu-state-probe.py" ] || cp -f "$R/tools/gpu-state-probe.py" "$RT/" 2>/dev/null || true
[ -f "$RT/gamma-ctx-probe.py" ] || { log "ABORT: gamma-ctx-probe.py not found on the head"; exit 1; }
touch "$L"; log "latch raised"
docker logs dsv41-head 2>&1 | tail -500 > "$W/results/logs-prewindow.txt"
# Stage the hidden-slow-state discriminator on every node: it can only run with the engine DOWN
# (8M pool + 0.90 static fraction => OOM alongside the engine; WINDOW-PREFILL-CHUNK.md trap 3).
for h in 10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4; do
  scp -q -o StrictHostKeyChecking=no -o ConnectTimeout=6 "$RT/gpu-state-probe.py" \
      "bertholomus@$h:/tmp/window/gpu-state-probe.py" 2>/dev/null || true
done

gpu_state() {  # best-effort annotation, run only while the engine is stopped
  for h in 10.0.0.1 10.0.0.2 10.0.0.3 10.0.0.4; do
    timeout 180 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=6 "bertholomus@$h" \
      "docker run --rm --gpus all --entrypoint python3 dsv41-4x-spark:local /tmp/window/gpu-state-probe.py --json" \
      > "$W/results/gpustate-$1-$h.log" 2>&1 || true
    log "$1: gpu-state $h -> $(tr -d '\n' < "$W/results/gpustate-$1-$h.log" | tail -c 160)"
  done
}

set_gamma() {  # $1 = value
  sed -i "s|^DSPARK_BLOCK_SIZE=.*|DSPARK_BLOCK_SIZE=$1|" "$ENVF"
  grep -n '^DSPARK_BLOCK_SIZE=' "$ENVF" | tee -a "$W/runner.log"
}
restore_env() { cp -a "$W/.env.tp4.orig" "$ENVF"; grep -n '^DSPARK_BLOCK_SIZE=' "$ENVF" | tee -a "$W/runner.log"; }

boot() {  # $1 = arm label
  local t0 elapsed
  systemctl --user reset-failed "$U" >>"$W/runner.log" 2>&1
  t0=$(date +%s)
  # stop -> (engine-down annotation) -> start, so the GPU-state probe never races the engine
  systemctl --user stop "$U" >>"$W/runner.log" 2>&1
  sleep 20
  gpu_state "$1"
  systemctl --user start "$U" >>"$W/runner.log" 2>&1
  log "$1: start issued"
  for _ in $(seq 1 110); do          # up to 27.5 min
    sleep 15
    if [ "$(health)" = "200" ] && docker logs dsv41-head 2>&1 | tail -400 | grep -q "fired up and ready to roll"; then
      log "$1: ready after $(( $(date +%s) - t0 ))s"
      docker logs dsv41-head 2>&1 | grep -m1 'server_args=' | tr ',' '\n' \
        | grep -E "'speculative_num_draft_tokens'|'max_total_tokens'|'mem_fraction_static'|'chunked_prefill_size'" | tee -a "$W/runner.log"
      # gamma resolved by the engine decides whether the arm is the arm we asked for
      docker logs dsv41-head 2>&1 | grep -oE 'gamma=[0-9]+|verify_num_draft_tokens=[0-9]+' | sort -u | tee -a "$W/runner.log"
      return 0
    fi
  done
  log "$1: BOOT FAILED (no ready in 27.5 min)"
  return 1
}

warm() { log "$1: warming"; timeout 900 python3 "$RT/warm-lane.py" --budget-s 300 > "$W/results/$1-warm.log" 2>&1
         tail -3 "$W/results/$1-warm.log" | tee -a "$W/runner.log"; }

gate() { timeout 600 python3 "$RT/batchtest.py" > "$W/results/$1-gate.log" 2>&1
         tail -2 "$W/results/$1-gate.log" | tee -a "$W/runner.log"; }

probe() {  # $1 = arm label
  timeout 1200 python3 "$PROBE" --label "$1" --out "$W/results/$1.json" >> "$W/runner.log" 2>&1
  local rc=$?
  docker logs dsv41-head > "$W/results/logs-$1.txt" 2>&1
  curl -s --max-time 20 http://127.0.0.1:8000/metrics > "$W/results/metrics-$1.txt" 2>/dev/null
  log "$1: probe exit $rc"
  return $rc
}

probe_live() {  # control arm without a reboot: the live lane IS the control config
  timeout 1200 python3 "$PROBE" --label "$1" --out "$W/results/$1.json" >> "$W/runner.log" 2>&1
  local rc=$?
  docker logs dsv41-head > "$W/results/logs-$1.txt" 2>&1
  curl -s --max-time 20 http://127.0.0.1:8000/metrics > "$W/results/metrics-$1.txt" 2>/dev/null
  log "$1 (live): probe exit $rc"
  return $rc
}

# ---------- arm control (live) ----------
log "--- arm C (control: live config, asserted gamma=3) ---"
warm C; gate C
if ! probe_live C; then log "ABORT: control probe failed on the live lane"; exit 1; fi

declare -A OK
# ---------- arm 5 ----------
log "--- arm G5: DSPARK_BLOCK_SIZE=5 ---"
set_gamma 5
if boot G5; then warm G5; gate G5; probe G5 && OK[G5]=1 || OK[G5]=0; else OK[G5]=0; fi

# ---------- arm 7 ----------
log "--- arm G7: DSPARK_BLOCK_SIZE=7 ---"
set_gamma 7
if boot G7; then warm G7; gate G7; probe G7 && OK[G7]=1 || OK[G7]=0; else OK[G7]=0; fi

# ---------- decide ----------
decide() {  # $1 = candidate label ; prints "<primary96> <primary96_control>"
  python3 - "$W/results/C.json" "$W/results/$1.json" <<'PY'
import json, sys
a = json.load(open(sys.argv[1]))["summary"]; b = json.load(open(sys.argv[2]))["summary"]
def g(s, k): 
    v = s.get(k, {}).get("primary")
    return v if v is not None else 0.0
print(f"{g(b,'96k'):.2f} {g(a,'96k'):.2f} {g(b,'32k'):.2f} {g(a,'32k'):.2f}")
PY
}

BEST="3"; BESTLABEL="control"
for arm in G5 G7; do
  if [ "${OK[$arm]:-0}" != "1" ]; then log "$arm: unusable (boot or probe failure)"; continue; fi
  read -r P96 C96 P32 C32 < <(decide "$arm" 2>>"$W/runner.log" || echo "0 0 0 0")
  GAIN96=$(python3 -c "print(f'{100*(${P96}-${C96})/max(${C96},0.001):.1f}')")
  GAIN32=$(python3 -c "print(f'{100*(${P32}-${C32})/max(${C32},0.001):.1f}')")
  log "$arm: primary96=${P96} vs control ${C96} (${GAIN96}%)  |  primary32=${P32} vs ${C32} (${GAIN32}%)"
  if python3 -c "import sys; sys.exit(0 if float('${GAIN96}')>=8.0 and float('${GAIN32}')>=3.0 else 1)"; then
    if [ "$BEST" = "3" ] || python3 -c "import sys; sys.exit(0 if float('${P96}')>float('${BEST96:-0}') else 1)"; then
      BEST="${arm#G}"; BESTLABEL="$arm"; BEST96=$P96
      log "$arm qualifies -> current best = $BEST"
    fi
  else
    log "$arm does not clear the rule (>= +8% @96k and >= +3% @32k)"
  fi
done

# ---------- settle on the winner, verified ----------
live_gamma=$(grep -oE '^DSPARK_BLOCK_SIZE=[0-9]+' "$ENVF" | cut -d= -f2)
log "decision: BEST=$BEST (live now: $live_gamma, live label: control if 3)"
if [ "$BEST" != "$live_gamma" ]; then
  log "settle boot: setting gamma=$BEST and rebooting"
  set_gamma "$BEST"; boot "SETTLE" && warm SETTLE && gate SETTLE && probe_live SETTLE || log "SETTLE boot issue"
else
  log "no settle boot needed; live config already the decision"
fi

{
  echo "gamma window $STAMP"
  echo "decision: gamma=$BEST ($BESTLABEL)"
  echo "rule: adopt if >=+8% primary@96k and >=+3% primary@32k vs control"
  grep -n '^DSPARK_BLOCK_SIZE=' "$ENVF"
  echo "env md5 after: $(md5sum "$ENVF" | cut -d' ' -f1)  (before: $(md5sum "$W/.env.tp4.orig" | cut -d' ' -f1))"
  echo "backup of pre-window env: $W/.env.tp4.orig"
  for f in C G5 G7; do [ -f "$W/results/$f.json" ] && python3 -c "
import json;d=json.load(open('$W/results/$f.json'));print('$f', d['summary'], 'failures=%d'%len(d['failures']))"; done
} > "$W/SUMMARY.txt"
cat "$W/SUMMARY.txt" | tee -a "$W/runner.log"

log "final health: $(health)"
log "watchdog state: $(cat "$RT/watchdog-state.json" 2>/dev/null | tr -d '\n')"
log "latch files: $(ls "$RT"/*.latch 2>/dev/null || echo none)"
log "=== gamma window end ==="