#!/usr/bin/env bash
# Adopt-or-start for the A1-A4 SGLang TP4 lane (DeepSeek-V4.1-Flash).
# Same discipline as the legacy vLLM controller: adopt a healthy live stack, never
# restart one that is already serving; fail closed on the durable latch.
set -euo pipefail

ROOT="/home/bertholomus/ai/runtime/deepseek-v41-flash-a1a4-sglang"
# Case-robust recipe resolution. The checkout on disk is named
# "DeepSeek-v4.1-Flash-DGX-Sparks" (lowercase v); a hard-coded uppercase "V"
# here made every fresh start fail with a bare `cd: No such file or directory`
# and looked like systemd/mount-namespace blindness. Resolve, do not hard-code.
RECIPE=""
for _c in /home/bertholomus/ai/recipes/*eepSeek-[vV]4.1-[fF]lash-DGX-Sparks; do
  [ -d "$_c" ] || continue
  RECIPE="$_c"
  break
done
if [ -z "$RECIPE" ]; then
  RECIPE="/home/bertholomus/ai/recipes/DeepSeek-v4.1-Flash-DGX-Sparks"
  echo "WARNING: no recipes/*eepSeek-*4.1-*lash-DGX-Sparks directory matched; falling back to $RECIPE" >&2
fi
LATCH="$ROOT/restart-inhibit.latch"
LOCK="$ROOT/.start.lock"
LEGACY_LATCH="/home/bertholomus/ai/runtime/deepseek-v41-a1a4-candidate/restart-inhibit.latch"
SERVED_MODEL="deepseek-v4.1-flash"
CONTEXT_LEN="1048576"

log() { printf '%s %s\n' "$(date -Is)" "$*"; }

if [[ -e "$LATCH" ]]; then
  log "durable latch present ($LATCH) — refusing to start"
  exit 1
fi

# Exactly one controller may own :8000. The legacy vLLM lane is the rollback path and
# is latched; if someone unlatches it while we are up, that is a conflict, not a race.
if [[ ! -e "$LEGACY_LATCH" ]] && systemctl --user is-active --quiet deepseek-v41-a1a4-tp4.service; then
  log "legacy deepseek-v41-a1a4-tp4.service is unlatched and active — refusing to compete for :8000"
  exit 1
fi

healthy() {
  local body
  body="$(curl -fsS --max-time 8 "http://127.0.0.1:8000/v1/models" 2>/dev/null)" || return 1
  python3 - "$body" "$SERVED_MODEL" "$CONTEXT_LEN" <<'PY'
import json, sys
body, served, ctx = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    rows = [x for x in json.loads(body).get("data", []) if x.get("id") == served]
except Exception:
    raise SystemExit(1)
if len(rows) != 1 or rows[0].get("max_model_len") != ctx:
    raise SystemExit(1)
PY
  curl -fsS --max-time 8 "http://127.0.0.1:8000/health" >/dev/null 2>&1 || return 1
}

if healthy; then
  log "adopted: $SERVED_MODEL already serving on :8000 with the expected identity"
  if [[ ! -e "$ROOT/tp4-images.json" ]]; then
    python3 "$ROOT/record-images.py" || log "warning: could not record the image manifest"
  fi
  exit 0
fi

exec 9>"$LOCK"
if ! flock -n 9; then
  log "another start is in flight (.start.lock held) — exiting"
  exit 0
fi

log "starting the SGLang TP4 stack from $RECIPE"
# Observed twice on spark1: the recipe directory (stable inode+birth) returns
# 'No such file or directory' for cd/ls from a systemd context, then is fine minutes
# later. Treat as transient: retry with backoff before giving up.
for i in 1 2 3 4 5 6; do
  cd "$RECIPE" 2>/dev/null && break
  log "attempt $i: recipe dir not visible yet — retrying in $((i*15))s"
  sleep $((i*15))
done
cd "$RECIPE" || { log "recipe dir still not visible after 6 attempts"; exit 1; }
./start-tp4.sh serve 2>&1 | tee "$ROOT/serve-$(date +%Y%m%dT%H%M%S).log"

if healthy; then
  log "start complete and verified"
  python3 "$ROOT/record-images.py" || log "warning: could not record the image manifest"
  # A cold TP4 is measurably slow until the Engram host cache and the shard page cache
  # fill (conc1 16 -> 34 tok/s). Warm it deliberately instead of taxing real users.
  log "warming the lane (engram host cache + shard page cache)"
  timeout 420 python3 "$ROOT/warm-lane.py" --budget-s 300 || log "warm pass ended without plateau (non-fatal)"
  exit 0
fi
log "start finished but the endpoint did not verify"
exit 1