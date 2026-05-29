#!/bin/bash
# Test reddit_token_auto_refresh.sh proactive-rotation gate logic.
# Verifies: token alive AND ttl > threshold = exit 0 no rotation.
#           token alive AND ttl < threshold = falls through to recovery chain.
# Mocks reddit_token_check.py by prepending a fake "alive (+Xh)" responder.

set -u
PASS=0; FAIL=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [ "${expected}" = "${actual}" ]; then
    echo "  ✓ ${label}"
    PASS=$((PASS+1))
  else
    echo "  ✗ ${label}: expected '${expected}', got '${actual}'"
    FAIL=$((FAIL+1))
  fi
}

# Set up isolated working dir
TMP="$(mktemp -d)"
trap "rm -rf '${TMP}'" EXIT

# Copy script + create fake check
cp ~/.config/mundo-bot/reddit_token_auto_refresh.sh "${TMP}/refresh.sh"
mkdir -p "${TMP}/.config/mundo-bot" "${TMP}/Library/Logs/mundo-bot"

# Substitute the script's CHECK var so it calls our fake. The script invokes
# CHECK via `python3 ${CHECK}` so the fake MUST be a Python file. RECOVER same.
sed -i.bak "s#CHECK=\".*reddit_token_check.py\"#CHECK=\"${TMP}/fake_check.py\"#" "${TMP}/refresh.sh"
sed -i.bak "s#RECOVER=\".*reddit_token_recover.py\"#RECOVER=\"${TMP}/fake_recover.py\"#" "${TMP}/refresh.sh"
sed -i.bak "s#LOG=\".*\"#LOG=\"${TMP}/log\"#" "${TMP}/refresh.sh"

# CRITICAL: route HOME to TMP so chrome_open_or_focus.sh path resolves to
# ${TMP}/.config/morning-workflow/chrome_open_or_focus.sh — which we stub
# with a no-op. Without this, step 3 invokes the REAL helper and spawns a
# real reddit.com tab in Chrome (test 8 caused this 2026-05-29).
mkdir -p "${TMP}/.config/morning-workflow"
cat > "${TMP}/.config/morning-workflow/chrome_open_or_focus.sh" << 'EOF'
#!/bin/bash
echo "stub: would have opened $1"
exit 0
EOF
chmod +x "${TMP}/.config/morning-workflow/chrome_open_or_focus.sh"

# Stub notify.py too (script pipes alert into it in step 4)
cat > "${TMP}/.config/morning-workflow/notify.py" << 'EOF'
import sys
sys.stdin.read()  # consume
sys.exit(0)
EOF

# Fake recover (Python) — silent no-op
cat > "${TMP}/fake_recover.py" << 'EOF'
import sys
sys.exit(0)
EOF

run() {
  # Run the script under fake check output. Pass single output → reused for
  # all is_alive calls. Pass `first|later` → calls 1+2 (ttl_hours + top
  # is_alive) return first, calls 3+ return later — lets us simulate "alive
  # at top, dead after step 1+2 fail" so step 3 guards are reachable.
  local fake_output="$1"
  local first_output="${fake_output%%|*}"
  local later_output="${fake_output#*|}"
  if [ "${fake_output}" = "${first_output}" ]; then
    later_output="${first_output}"
  fi
  local switch_after="${2:-2}"  # default: first 2 calls return first_output
  rm -f "${TMP}/check_call_count"
  # Python fake — script invokes via `python3`, so .py mandatory.
  # First ${switch_after} calls return first_output; subsequent calls return later_output.
  cat > "${TMP}/fake_check.py" << EOF
import os, sys
COUNT_FILE = "${TMP}/check_call_count"
n = 0
if os.path.exists(COUNT_FILE):
    n = int(open(COUNT_FILE).read().strip() or "0")
n += 1
open(COUNT_FILE, "w").write(str(n))
if n <= ${switch_after}:
    print("${first_output}")
else:
    print("${later_output}")
sys.exit(0)
EOF
  # Silence script output; only return its exit code via the trailing echo.
  HOME="${TMP}" PROACTIVE_HOURS="${PROACTIVE_HOURS:-4}" REDDIT_AUTO_OPEN_CHROME="${REDDIT_AUTO_OPEN_CHROME:-0}" bash "${TMP}/refresh.sh" >/dev/null 2>&1
  echo $?
}

echo "Test 1: token alive +13h (well above threshold) → exit 0 no rotation"
rc=$(run "reddit-token-check: alive (+13.75h)")
assert_eq "exit code 0" "0" "${rc}"
assert_eq "log shows no action" "1" "$(grep -c "no action" "${TMP}/log" 2>/dev/null || echo 0)"

echo "Test 2: token alive +2h (below 4h threshold) → triggers rotation chain"
rm -f "${TMP}/log"
rc=$(run "reddit-token-check: alive (+2.50h)")
assert_eq "exit code 0" "0" "${rc}"
assert_eq "log shows below-threshold trigger" "1" "$(grep -c "forcing rotation" "${TMP}/log" 2>/dev/null || echo 0)"

echo "Test 3: token alive +0.4h (below default 4h) → triggers rotation"
rm -f "${TMP}/log"
rc=$(run "reddit-token-check: alive (+0.40h)")
assert_eq "exit code 0" "0" "${rc}"
assert_eq "log shows below-threshold trigger" "1" "$(grep -c "forcing rotation" "${TMP}/log" 2>/dev/null || echo 0)"

echo "Test 4: custom PROACTIVE_HOURS=2 with token at +3h → no action"
rm -f "${TMP}/log"
rc=$(PROACTIVE_HOURS=2 run "reddit-token-check: alive (+3.00h)")
assert_eq "exit code 0" "0" "${rc}"
assert_eq "log shows no action" "1" "$(grep -c "no action" "${TMP}/log" 2>/dev/null || echo 0)"

echo "Test 5: token DEAD → enters recovery chain"
rm -f "${TMP}/log"
rc=$(run "reddit-token-check: DEAD (-1.2h)")
assert_eq "log shows START line" "1" "$(grep -c "=== START" "${TMP}/log" 2>/dev/null || echo 0)"

echo "Test 6: proactive mode → step 3 SKIPPED proactive, no Chrome touched"
rm -f "${TMP}/log" "${TMP}/check_call_count"
rc=$(run "reddit-token-check: alive (+2.50h)|reddit-token-check: DEAD (-0.1h)")
assert_eq "step 3 skipped (proactive)" "1" "$(grep -c "SKIPPED — proactive mode" "${TMP}/log" | head -1)"
assert_eq "no chrome_open_or_focus call" "0" "$(grep -c "chrome_open_or_focus" "${TMP}/log" | head -1)"

echo "Test 7: DEAD + step 3 default OFF → no Chrome tab opened, falls to step 4"
rm -f "${TMP}/log" "${TMP}/check_call_count" "${TMP}/.config/mundo-bot/.last_step3_ts"
mkdir -p "${TMP}/.config/mundo-bot"
rc=$(run "reddit-token-check: DEAD (-1.0h)")
assert_eq "step 3 skipped via env flag" "1" "$(grep -c "REDDIT_AUTO_OPEN_CHROME" "${TMP}/log" | head -1)"
assert_eq "no chrome_open_or_focus call" "0" "$(grep -c "chrome_open_or_focus" "${TMP}/log" | head -1)"
assert_eq "no nudge log line" "0" "$(grep -c "step 3: nudging Chrome" "${TMP}/log" | head -1)"

echo "Test 8: DEAD + REDDIT_AUTO_OPEN_CHROME=1 + old stamp → step 3 runs"
rm -f "${TMP}/log" "${TMP}/check_call_count"
old_ts=$(( $(date +%s) - 8*3600 ))
echo "${old_ts}" > "${TMP}/.config/mundo-bot/.last_step3_ts"
rc=$(REDDIT_AUTO_OPEN_CHROME=1 run "reddit-token-check: DEAD (-1.0h)")
assert_eq "step 3 nudge line present" "1" "$(grep -c "step 3: nudging Chrome" "${TMP}/log" | head -1)"

echo "Test 9: DEAD + REDDIT_AUTO_OPEN_CHROME=1 + recent stamp → cooldown skip"
rm -f "${TMP}/log" "${TMP}/check_call_count"
date +%s > "${TMP}/.config/mundo-bot/.last_step3_ts"
rc=$(REDDIT_AUTO_OPEN_CHROME=1 run "reddit-token-check: DEAD (-1.0h)")
assert_eq "cooldown skip line" "1" "$(grep -c "last ran" "${TMP}/log" | head -1)"
assert_eq "no nudge during cooldown" "0" "$(grep -c "step 3: nudging Chrome" "${TMP}/log" | head -1)"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL}" = "0" ]
