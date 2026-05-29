#!/bin/bash
# Run all mundo-bot tests. Non-zero exit on any failure.
# Usage: bash ~/.config/mundo-bot/tests/run_all.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== test_moltbook_key_check.py ==="
/usr/bin/python3 "${DIR}/test_moltbook_key_check.py" 2>&1 | grep -E "Ran |FAIL|OK"

echo ""
echo "=== test_mundo_ab_closer.py ==="
/usr/bin/python3 "${DIR}/test_mundo_ab_closer.py" 2>&1 | grep -E "Ran |FAIL|OK"

echo ""
echo "=== test_reddit_token_refresh.sh ==="
bash "${DIR}/test_reddit_token_refresh.sh" 2>&1 | tail -3
