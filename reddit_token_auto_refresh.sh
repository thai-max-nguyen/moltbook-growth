#!/bin/bash
# reddit_token_auto_refresh.sh — autonomous Reddit cookie token recovery.
#
# Runs on cron. If token_v2 JWT is dead, walks the full recovery chain:
#   1. Try recover from Chrome SQLite cookie DB (normal path)
#   2. If Chrome holds writes in WAL → force a checkpoint read by
#      bumping Chrome to load reddit.com (AppleScript) + wait
#   3. If cookie still expired → launch reddit.com in foreground Chrome
#      so a logged-in session is forced to mint a fresh JWT
#   4. If after all that token is still dead → email alert so user
#      knows to log in manually
#
# Hooked into cron via:
#   */30 7-23 * * * /bin/bash ~/.config/mundo-bot/reddit_token_auto_refresh.sh
#
# Idempotent: probe + only acts if token is dead. No-op cost when alive.

set -u
LOG="${HOME}/Library/Logs/mundo-bot/reddit_token_auto_refresh.log"
mkdir -p "$(dirname "${LOG}")"

TS() { date "+[%Y-%m-%d %H:%M:%S]"; }
log() { echo "$(TS) $*" >> "${LOG}"; }

PY="/usr/bin/python3"
RECOVER="${HOME}/.config/mundo-bot/reddit_token_recover.py"
CHECK="${HOME}/.config/mundo-bot/reddit_token_check.py"

is_alive() {
  "${PY}" "${CHECK}" 2>&1 | grep -q "alive"
}

if is_alive; then
  log "token alive — no action"
  exit 0
fi

log "=== START: token DEAD, beginning auto-recovery chain ==="

# ── Step 1: simple recover from Chrome SQLite
log "step 1: simple recover from Chrome cookies"
"${PY}" "${RECOVER}" >> "${LOG}" 2>&1 || true
if is_alive; then
  log "✓ step 1 succeeded — token alive"
  exit 0
fi

# ── Step 2: copy Chrome WAL files (Cookies-wal + -shm) into a recover-
# friendly location, then re-run recover. Chrome holds writes in WAL until
# checkpoint; the standard recover script only reads the main DB.
log "step 2: WAL-aware re-read of Chrome cookies"
SRC="${HOME}/Library/Application Support/Google/Chrome/Default"
TMPDIR=$(mktemp -d)
for f in Cookies Cookies-journal Cookies-wal Cookies-shm; do
  if [ -e "${SRC}/${f}" ]; then
    cp "${SRC}/${f}" "${TMPDIR}/${f}" 2>/dev/null || true
  fi
done
# Sniff token_v2 from the WAL-bundled DB
"${PY}" - <<PYEOF >> "${LOG}" 2>&1 || true
import sqlite3, subprocess, hashlib, json, os, sys, time, base64
from Crypto.Cipher import AES

tmp = "${TMPDIR}/Cookies"
con = sqlite3.connect(tmp)
con.execute("PRAGMA wal_checkpoint(FULL);")
row = con.execute(
    "SELECT encrypted_value FROM cookies "
    "WHERE host_key LIKE '%reddit.com%' AND name='token_v2'"
).fetchone()
con.close()
if not row:
    sys.exit("no token_v2 in WAL DB")

key = subprocess.check_output(
    ["security","find-generic-password","-w","-a","Chrome","-s","Chrome Safe Storage"]).strip()
aes_key = hashlib.pbkdf2_hmac("sha1", key, b"saltysalt", 1003, dklen=16)
dec = AES.new(aes_key, AES.MODE_CBC, IV=b"\x20"*16).decrypt(bytes(row[0])[3:])
pad = dec[-1]
if 1 <= pad <= 16: dec = dec[:-pad]
val = dec[32:].decode("utf-8", errors="ignore")
if not (val.startswith("eyJ") and val.count(".") == 2):
    sys.exit("token_v2 malformed")
payload = val.split(".")[1] + "===="
exp = json.loads(base64.urlsafe_b64decode(payload[:len(payload)//4*4]))["exp"]
delta_h = (exp - time.time()) / 3600
if delta_h < 0:
    sys.exit(f"WAL token still expired by {-delta_h:.1f}h")

cfg_path = os.path.expanduser("~/.config/mundo-bot/reddit_config.json")
cfg = json.load(open(cfg_path))
cfg["token_v2"] = val
cfg["token_expires"] = exp
json.dump(cfg, open(cfg_path, "w"), indent=2)
print(f"step 2 ok: token refreshed from WAL, +{delta_h:.1f}h")
PYEOF
rm -rf "${TMPDIR}"
if is_alive; then
  log "✓ step 2 succeeded — token alive"
  exit 0
fi

# ── Step 3: force Chrome to load reddit.com (will mint fresh JWT if
# session still authenticated). Then wait + retry recover.
# Uses chrome_open_or_focus.sh to focus + reload an existing reddit tab if
# one is already open — prevents the multi-tab pileup bug (this cron fires
# every 30min; pre-fix it spawned a new tab per fire = ~15 dupes/day).
log "step 3: nudging Chrome to load reddit.com (focus existing tab if any)"
OPEN_HELPER="${HOME}/.config/morning-workflow/chrome_open_or_focus.sh"
if [ -x "${OPEN_HELPER}" ]; then
  RESULT=$("${OPEN_HELPER}" "https://www.reddit.com/" "reddit.com" 2>&1 || echo "helper-err")
  log "  chrome_open_or_focus: ${RESULT}"
else
  log "  ⚠ ${OPEN_HELPER} missing — falling back to legacy new-tab open"
  /usr/bin/open -ga "Google Chrome" "https://www.reddit.com/"
fi
sleep 8
"${PY}" "${RECOVER}" >> "${LOG}" 2>&1 || true
if is_alive; then
  log "✓ step 3 succeeded — token alive (Chrome minted fresh JWT)"
  exit 0
fi

# ── Step 4: failure — email Max so they know to log in manually
log "✗ all auto-recovery steps failed — token still dead"
cat <<EOF | "${PY}" "${HOME}/.config/morning-workflow/notify.py" 2>/dev/null || \
  echo "  (notify queue failed; running mailtool fallback)"
[Alert] Reddit token DEAD — auto-recovery exhausted

The Reddit browser-cookie JWT for Initial-Process-2875 has expired
and could not be auto-renewed. Steps tried:
  1. Direct cookie recover — failed
  2. WAL-aware re-read — failed
  3. Chrome reload of reddit.com — failed

You probably need to manually log in:
  1. Open Chrome → https://www.reddit.com/logout
  2. Then https://www.reddit.com/login/
  3. Sign in with Initial-Process-2875 (username+password, NOT Google SSO)
  4. Wait for home feed to load
  5. Run: python3 ~/.config/mundo-bot/reddit_token_recover.py

Log: ${LOG}
EOF

log "alert email queued — exiting"
exit 1
