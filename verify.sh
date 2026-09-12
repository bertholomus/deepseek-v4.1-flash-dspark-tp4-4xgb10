#!/usr/bin/env bash
# Post-deploy gates for the DSV41-Flash TP4 Spark recipe.
# Run on spark1 (the lane head) against a QUIET, WARM lane. Other clients' traffic
# invalidates results. Gate harnesses live in the lane dir:
#   ~/ai/runtime/deepseek-v41-flash-a1a4-sglang/{batchtest.py,prefill_distinct.py}
# Usage: bash verify.sh [needle_depth]   (default 136000)
set -u
DEPTH="${1:-136000}"
GW=${GW:-http://127.0.0.1:8000}
H="$HOME/ai/runtime/deepseek-v41-flash-a1a4-sglang"

pass=0; fail=0
ok(){ echo "  PASS  $*"; pass=$((pass+1)); }
no(){ echo "  FAIL  $*"; fail=$((fail+1)); }

echo "== 1. health =="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$GW/health")
[ "$code" = "200" ] && ok "health=$code" || no "health=$code (expected 200)"

echo "== 2. served identity =="
m=$(curl -s --max-time 20 "$GW/v1/models")
echo "$m" | grep -q '"id":"deepseek-v4.1-flash"' && ok "served name unchanged" || no "served name missing: $m"
echo "$m" | grep -q '"max_model_len":1048576' && ok "context_len=1048576" || no "unexpected max_model_len: $m"

echo "== 3. garbled-output gate =="
if [ -f "$H/batchtest.py" ]; then
  out=$(python3 "$H/batchtest.py" 2>&1 | tail -8)
  echo "$out"
  echo "$out" | grep -q "GATE: garbled=0" && ok "0 garbled" || no "garbled gate failed — read output above"
else
  no "batchtest.py not found at $H"
fi

echo "== 4. needle-in-haystack gate (depth $DEPTH) =="
if [ -f "$H/prefill_distinct.py" ]; then
  out=$(python3 "$H/prefill_distinct.py" "$DEPTH" 1 2>&1 | tail -8)
  echo "$out"
  echo "$out" | grep -q "PELICAN-1" && ok "needle answered" || no "needle failed/inconclusive"
else
  no "prefill_distinct.py not found at $H"
fi

echo
echo "== SUMMARY: $pass passed, $fail failed =="
[ "$fail" -eq 0 ] || exit 1