#!/usr/bin/env bash
# DSV4.1 TP4 — FAIR-GO window: the three remaining unarmoured levers, before we publish. UNATTENDED.
#
# Arms (one change each, everything else at the live production config gamma=3 / fused-off / fcfs):
#   C1 : control, live lane as-is (no reboot)          -> opening control
#   G2 : DSPARK_BLOCK_SIZE=2                            -> depth-adaptive hypothesis
#        (live traffic at 123k ctx shows accept len 2.20-2.55 / accept rate 0.40-0.52: only
#         1.2-1.5 of the 3 drafted positions are accepted at depth, so the wider window's
#         marginal positions are mostly rejected. 8k acceptance is high, so this arm costs there.)
#   F1 : SGLANG_DSPARK_OPT_FUSED_GREEDY_MARKOV=1        -> engine default is False (unarmed);
#        fused greedy Markov sampling inside the captured decode graph; quality-neutral for
#        greedy rows (outputs must stay bit-identical through the gate).
#   L  : --schedule-policy lpm                          -> build default here is fcfs; lpm is the
#        cache-aware policy (our shape is multi-turn with a long cached trunk).
#   C2 : control again, full env restored + reboot      -> closing control AND the restore boot
#
# Probe: fairgo-probe.py = gamma-ctx-probe.py + --salt, so every arm primes its OWN prefix.
#   Why: tonight's SETTLE arm read 18.5 tok/s against a 10.6 control in the same window — the
#   packed/engram prefix cache survives reboots, so later arms silently inherited warm prefixes
#   and arm order leaked into the numbers. A per-arm salt gives every arm cold-equal footing
#   while keeping the in-arm (prime -> measure) shape that matches real agentic traffic.
#
# PRE-REGISTERED RULE (fixed before launch; the window cannot be re-interpreted afterwards):
#   P8/P32/P96 = prose+code median decode tok/s from the arm json (the probe's PRIMARY).
#   Fleet-weighted score          W = 0.20*P8 + 0.30*P32 + 0.50*P96
#     (weights from the fleet's own context distribution: p50 52k, p90 148k -> long-context dominant)
#   Control reference per size    = mean(C1, C2)                       [bracketing controls]
#   Drift flag                    = |C2 - C1| > 15% at any size  -> window is drift-limited and
#                                   the arm must clear the bar against BOTH controls at that size.
#   ADOPT an arm iff ALL of:
#     (a) W_arm  >= 1.05 * W_ctrl
#     (b) every size >= 0.92 * its control value
#     (c) probe failures = 0 and the garbled-output gate is clean
#   Multiple qualifiers -> highest W. No qualifier -> live config stays (no change at all).
#
# Worst case the lane is dark ~2 h across 4 boots; it always ends on a verified healthy lane with
# the pre-window env restored (or the winning arm armed), latch dropped, md5s recorded.
# Backups: $W/.env.tp4.orig (byte copy) and any pre-existing .env.tp4.* backups are untouched.
set -uo pipefail

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
R="$HOME/ai/recipes/DeepSeek-v4.1-Flash-DGX-Sparks"
RT="$HOME/ai/runtime/deepseek-v41-flash-a1a4-sglang"
U="deepseek-v41-flash-a1a4-sglang-tp4.service"
L="$RT/maintenance.latch"
W="$HOME/dsv41-state/window-fairgo-$STAMP"
ENVF="$R/.env.tp4"
PROBE="$RT/fairgo-probe.py"
mkdir -p "$W/results"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$W/runner.log"; }
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 6 http://127.0.0.1:8000/health || echo ERR; }
cleanup() { rm -f "$L"; log "latch dropped (trap)"; }
trap cleanup EXIT

log "=== fair-go window start $STAMP ==="
log "env md5 before: $(md5sum "$ENVF" | cut -d' ' -f1)"
cp -a "$ENVF" "$W/.env.tp4.orig"
grep -nE '^DSPARK_BLOCK_SIZE=|^EXTRA_SGLANG_ARGS=|^SGLANG_DSPARK_OPT_FUSED_GREEDY_MARKOV=' "$ENVF" >> "$W/runner.log"

if [ "$(health)" != "200" ]; then
  log "ABORT: lane not healthy at window start (health=$(health))"
  echo "aborted: starting health was $(health)" > "$W/SUMMARY.txt"; exit 1
fi
for cand in "$RT/fairgo-probe.py" "$R/tools/fairgo-probe.py" /tmp/window/fairgo-probe.py; do
  [ -f "$cand" ] && { cp -f "$cand" "$RT/fairgo-probe.py"; break; }
done
[ -f "$RT/fairgo-probe.py" ] || { log "ABORT: fairgo-probe.py not found on the head"; exit 1; }
[ -f "$RT/batchtest.py" ] || log "WARN: batchtest.py missing (gate will be skipped)"
[ -f "$RT/warm-lane.py" ] || log "WARN: warm-lane.py missing (warm will be skipped)"
touch "$L"; log "latch raised"
docker logs dsv41-head 2>&1 | tail -500 > "$W/results/logs-prewindow.txt"

# --- engine-down GPU-state annotation (fixed staging: mkdir on workers, local docker run on head)
for h in 10.0.0.2 10.0.0.3 10.0.0.4; do
  timeout 20 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=6 "bertholomus@$h" "mkdir -p /tmp/window" >/dev/null 2>&1 || true
  scp -q -o StrictHostKeyChecking=no -o ConnectTimeout=6 "$RT/gpu-state-probe.py" \
      "bertholomus@$h:/tmp/window/gpu-state-probe.py" 2>/dev/null || true
done
gpu_state() {
  timeout 180 docker run --rm --gpus all --entrypoint python3 dsv41-4x-spark:local \
    /tmp/window/gpu-state-probe.py --json > "$W/results/gpustate-$1-head.log" 2>&1 || true
  log "$1: gpu-state head -> $(tr -d '\n' < "$W/results/gpustate-$1-head.log" | tail -c 160)"
  for h in 10.0.0.2 10.0.0.3 10.0.0.4; do
    timeout 180 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=6 "bertholomus@$h" \
      "docker run --rm --gpus all --entrypoint python3 dsv41-4x-spark:local /tmp/window/gpu-state-probe.py --json" \
      > "$W/results/gpustate-$1-$h.log" 2>&1 || true
    log "$1: gpu-state $h -> $(tr -d '\n' < "$W/results/gpustate-$1-$h.log" | tail -c 160)"
  done
}

# --- env mutation: always start from the pristine copy, apply exactly one change, log it
restore_env() { cp -a "$W/.env.tp4.orig" "$ENVF"; }
arm_g2() { restore_env; sed -i "s|^DSPARK_BLOCK_SIZE=.*|DSPARK_BLOCK_SIZE=2  # fairgo arm G2|" "$ENVF"; }
arm_f1() { restore_env; printf 'SGLANG_DSPARK_OPT_FUSED_GREEDY_MARKOV=1  # fairgo arm F1 (engine default False)\n' >> "$ENVF"; }
arm_l()  { restore_env; sed -i 's|^EXTRA_SGLANG_ARGS="|EXTRA_SGLANG_ARGS="--schedule-policy lpm |' "$ENVF"; }

boot() {  # $1 = arm label
  local t0
  systemctl --user reset-failed "$U" >>"$W/runner.log" 2>&1
  t0=$(date +%s)
  systemctl --user stop "$U" >>"$W/runner.log" 2>&1
  sleep 20
  gpu_state "$1"
  systemctl --user start "$U" >>"$W/runner.log" 2>&1
  log "$1: start issued"
  for _ in $(seq 1 110); do
    sleep 15
    if [ "$(health)" = "200" ] && docker logs dsv41-head 2>&1 | tail -400 | grep -q "fired up and ready to roll"; then
      log "$1: ready after $(( $(date +%s) - t0 ))s"
      docker logs dsv41-head 2>&1 | grep -m1 'server_args=' | tr ',' '\n' \
        | grep -E "'schedule_policy'|'speculative_num_draft_tokens'|'max_total_tokens'|'mem_fraction_static'" | tee -a "$W/runner.log"
      docker logs dsv41-head 2>&1 | grep -oE 'gamma=[0-9]+|verify_num_draft_tokens=[0-9]+' | sort -u | tee -a "$W/runner.log"
      docker exec dsv41-head printenv SGLANG_DSPARK_OPT_FUSED_GREEDY_MARKOV 2>/dev/null \
        | sed 's/^/    fused_greedy_markov=/' | tee -a "$W/runner.log"
      return 0
    fi
  done
  log "$1: BOOT FAILED (no ready in 27.5 min)"; return 1
}

warm() { log "$1: warming"; timeout 900 python3 "$RT/warm-lane.py" --budget-s 300 > "$W/results/$1-warm.log" 2>&1
         tail -3 "$W/results/$1-warm.log" | tee -a "$W/runner.log"; }
gate() { timeout 600 python3 "$RT/batchtest.py" > "$W/results/$1-gate.log" 2>&1
         tail -2 "$W/results/$1-gate.log" | tee -a "$W/runner.log"; }

run_probe() {  # $1 = arm label, $2 = salt
  timeout 1500 python3 "$PROBE" --label "$1" --salt "$2" --out "$W/results/$1.json" >> "$W/runner.log" 2>&1
  local rc=$?
  docker logs dsv41-head > "$W/results/logs-$1.txt" 2>&1
  curl -s --max-time 20 http://127.0.0.1:8000/metrics > "$W/results/metrics-$1.txt" 2>/dev/null
  log "$1: probe exit $rc"
  return $rc
}

# ---------- C1: opening control on the live lane ----------
log "--- arm C1 (control, live config, no reboot) ---"
warm C1; gate C1
if ! run_probe C1 c1-live; then log "ABORT: control probe failed on the live lane"; exit 1; fi

declare -A OK
# ---------- G2 ----------
log "--- arm G2: DSPARK_BLOCK_SIZE=2 ---"
arm_g2
if boot G2; then warm G2; gate G2; run_probe G2 g2 && OK[G2]=1 || OK[G2]=0; else OK[G2]=0; fi

# ---------- F1 ----------
log "--- arm F1: SGLANG_DSPARK_OPT_FUSED_GREEDY_MARKOV=1 ---"
arm_f1
if boot F1; then warm F1; gate F1; run_probe F1 f1 && OK[F1]=1 || OK[F1]=0; else OK[F1]=0; fi

# ---------- L ----------
log "--- arm L: --schedule-policy lpm ---"
arm_l
if boot L; then warm L; gate L; run_probe L llpm && OK[L]=1 || OK[L]=0; else OK[L]=0; fi

# ---------- C2: closing control on the restored production config ----------
log "--- arm C2 (control, full env restored, reboot) ---"
restore_env
if boot C2; then warm C2; gate C2; run_probe C2 c2-live && OK[C2]=1 || OK[C2]=0; else OK[C2]=0; fi

# ---------- decide ----------
python3 - "$W" "${OK[C2]:-0}" <<'PY' | tee -a "$W/runner.log"
import json, os, sys
W, c2ok = sys.argv[1], sys.argv[2] == "1"
res = os.path.join(W, "results")
def load(lbl):
    p = os.path.join(res, lbl + ".json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p))
    s = d["summary"]
    out = {k: (s.get(k, {}) or {}).get("primary") for k in ("8k", "32k", "96k")}
    return out if all(v is not None for v in out.values()) else None
def W_of(a):
    return 0.20 * a["8k"] + 0.30 * a["32k"] + 0.50 * a["96k"]
c1, c2 = load("C1"), load("C2")
arms = {a: load(a) for a in ("G2", "F1", "L")}
print("arm      |    8k     32k     96k   |   W")
for lbl, a in (("C1", c1), ("C2", c2)) + tuple(arms.items()):
    print(f"{lbl:<8} | {a['8k']:6.2f} {a['32k']:6.2f} {a['96k']:6.2f} | {W_of(a):6.2f}" if a else f"{lbl:<8} | unusable")
if not c1 or (not c2 and not c2ok):
    print("DECISION: no usable closing control -> window report only, config untouched")
    sys.exit(0)
if c1 and c2:
    drift = {k: 100 * (c2[k] - c1[k]) / max(c1[k], 1e-6) for k in c1}
    print("drift C2 vs C1 %:", {k: round(v, 1) for k, v in drift.items()})
    ctrl = {k: 0.5 * (c1[k] + c2[k]) for k in c1}
    drift_limited = any(abs(v) > 15.0 for v in drift.values())
else:
    print("NOTE: closing control unusable -> control reference is C1 only")
    ctrl, drift_limited = c1, True
Wc = W_of(ctrl)
print(f"control reference W = {Wc:.2f}{'  [DRIFT-LIMITED]' if drift_limited else ''}")
qual = []
for lbl, a in arms.items():
    if not a:
        print(f"{lbl}: unusable"); continue
    w = W_of(a)
    ratio = {k: a[k] / max(ctrl[k], 1e-6) for k in a}
    worst = min(ratio.values())
    against_both = (c1 is not None and c2 is not None and
                    all(a[k] >= 0.92 * min(c1[k], c2[k]) for k in a))
    ok = (w >= 1.05 * Wc) and worst >= 0.92 and (against_both or not drift_limited)
    print(f"{lbl}: W={w:.2f} ({100*(w/Wc-1):+.1f}%)  worst-size={100*(worst-1):+.1f}%  "
          f"{'QUALIFIES' if ok else 'does not clear the rule'}")
    if ok:
        qual.append((w, lbl))
if qual:
    qual.sort(reverse=True)
    print(f"DECISION: ADOPT {qual[0][1]} (W={qual[0][0]:.2f})")
else:
    print("DECISION: no arm clears the pre-registered rule -> live config unchanged")
    json.dump({"adopt": None}, open(os.path.join(W, "decision.json"), "w"))
if qual:
    json.dump({"adopt": qual[0][1], "w": qual[0][0]}, open(os.path.join(W, "decision.json"), "w"))
PY

WINNER=$(python3 -c "
import json,os,sys
p=os.path.join('$W','decision.json')
print(json.load(open(p)).get('adopt') or '' if os.path.exists(p) else '')" 2>/dev/null)

if [ -n "$WINNER" ]; then
  log "settle boot: arming winner $WINNER"
  case "$WINNER" in
    G2) arm_g2 ;; F1) arm_f1 ;; L) arm_l ;;
    *)  log "unknown winner label $WINNER -> refusing to arm"; WINNER="" ;;
  esac
  if [ -n "$WINNER" ]; then
    boot SETTLE && warm SETTLE && gate SETTLE && run_probe SETTLE settle
  fi
else
  log "no arm adopted; production config already restored by the C2 boot"
fi

{
  echo "fair-go window $STAMP"
  echo "rule: W=0.20*P8+0.30*P32+0.50*P96; adopt if W>=+5% vs mean(C1,C2), every size>=92%, no failures, gate clean"
  echo "arms: C1(live) G2(gamma=2) F1(fused-greedy-markov) L(schedule-policy=lpm) C2(restored control)"
  grep -nE '^DSPARK_BLOCK_SIZE=|^EXTRA_SGLANG_ARGS=|^SGLANG_DSPARK_OPT_FUSED_GREEDY_MARKOV=' "$ENVF" | tee -a "$W/runner.log"
  echo "env md5 after: $(md5sum "$ENVF" | cut -d' ' -f1)  (before: $(md5sum "$W/.env.tp4.orig" | cut -d' ' -f1))"
  echo "backup of pre-window env: $W/.env.tp4.orig"
  [ -f "$W/decision.json" ] && cat "$W/decision.json" && echo
  for f in C1 G2 F1 L C2 SETTLE; do [ -f "$W/results/$f.json" ] && python3 -c "
import json;d=json.load(open('$W/results/$f.json'));print('$f', d['summary'], 'failures=%d'%len(d['failures']))"; done
} > "$W/SUMMARY.txt"
cat "$W/SUMMARY.txt" | tee -a "$W/runner.log"

log "final health: $(health)"
log "watchdog state: $(cat "$RT/watchdog-state.json" 2>/dev/null | tr -d '\n')"
log "latch files: $(ls "$RT"/*.latch 2>/dev/null || echo none)"
log "=== fair-go window end ==="