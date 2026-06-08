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

# Proactive rotation 2026-05-29: rotate when TTL drops below PROACTIVE_HOURS so
# engage/post never fire with a token about to expire mid-call (Reddit JWTs hard-
# expire at iat+24h with no grace). Default 4h.
PROACTIVE_HOURS="${PROACTIVE_HOURS:-4}"

# Track if this run is proactive (token still alive but TTL low). Proactive
# runs skip step 3 because step 3 opens reddit.com in Chrome — firing every
# 30min spawned 8+ duplicate tabs across a 4h proactive window. Step 3 only
# fires when the token is TRULY dead.
PROACTIVE_RUN=0

ttl_hours() {
  "${PY}" "${CHECK}" 2>&1 | sed -nE 's/.*alive \(\+?([0-9.]+)h\).*/\1/p' | head -1
}

current_ttl="$(ttl_hours)"

if is_alive; then
  if [ -n "${current_ttl}" ]; then
    # Compare integer parts to avoid floating-point shell math.
    ttl_int="$(printf '%.0f' "${current_ttl}")"
    if [ "${ttl_int}" -ge "${PROACTIVE_HOURS}" ]; then
      log "token alive (+${current_ttl}h) — no action"
      exit 0
    fi
    PROACTIVE_RUN=1
    log "token alive (+${current_ttl}h) but below proactive ${PROACTIVE_HOURS}h threshold — forcing rotation (proactive mode)"
  else
    log "token alive — no action (ttl parse failed)"
    exit 0
  fi
fi

log "=== START: token DEAD or below proactive threshold, beginning recovery ==="

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
#
# DISABLED BY DEFAULT (2026-05-29). On multi-profile Chrome installs the
# AppleScript "focus existing tab" path is unreliable — it inspects only one
# profile's windows, fails to match tabs the user actually has open in
# another profile, and spawns a new tab on every fire. With LaunchAgent
# firing every 30min that produced 8-15 duplicate reddit.com tabs/day.
#
# Set REDDIT_AUTO_OPEN_CHROME=1 to re-enable. When disabled, recovery
# falls back to step 4 (alert) on cookie-only failure — user logs in
# manually once, subsequent fires extract the fresh cookie automatically.
STEP3_COOLDOWN_HOURS="${STEP3_COOLDOWN_HOURS:-6}"
STEP3_STAMP="${HOME}/.config/mundo-bot/.last_step3_ts"

if [ "${PROACTIVE_RUN}" = "1" ]; then
  log "step 3 SKIPPED — proactive mode (token still alive, no Chrome navigation)"
  log "✗ proactive rotation failed via cookie-only path — token still aging, will retry next fire"
  exit 0
fi

if [ "${REDDIT_AUTO_OPEN_CHROME:-0}" != "1" ]; then
  log "step 3 SKIPPED — REDDIT_AUTO_OPEN_CHROME!=1 (default off; see header for why)"
  log "  → falling through to step 4 alert; manual Chrome login required"
else
  now_epoch="$(date +%s)"
  last_step3=0
  [ -f "${STEP3_STAMP}" ] && last_step3="$(cat "${STEP3_STAMP}" 2>/dev/null || echo 0)"
  hours_since="$(( (now_epoch - last_step3) / 3600 ))"

  if [ "${hours_since}" -lt "${STEP3_COOLDOWN_HOURS}" ]; then
    log "step 3 SKIPPED — last ran ${hours_since}h ago (cooldown ${STEP3_COOLDOWN_HOURS}h). Skipping to step 4."
  else
    log "step 3: nudging Chrome to load reddit.com (REDDIT_AUTO_OPEN_CHROME=1)"
    echo "${now_epoch}" > "${STEP3_STAMP}"
    OPEN_HELPER="${HOME}/.config/morning-workflow/chrome_open_or_focus.sh"
    if [ -x "${OPEN_HELPER}" ]; then
      RESULT=$("${OPEN_HELPER}" "https://www.reddit.com/" "reddit.com" 2>&1 || echo "helper-err")
      log "  chrome_open_or_focus: ${RESULT}"
    else
      log "  ⚠ ${OPEN_HELPER} missing — falling back to legacy new-tab open"
      /usr/bin/open -ga "Google Chrome" "https://www.reddit.com/"
    fi
    # 2026-06-08: the old `sleep 8` + single recover was too short. reddit
    # only mints a fresh token_v2 after its in-session bootstrap runs, and
    # Chrome flushes the new cookie to the main DB a few seconds later. On a
    # cold/windowless Chrome (helper returns "opened-new-window") the page
    # needs ~15-20s to load + mint + flush. Wait longer, then poll recover a
    # few times before giving up. Verified: in-session reload mints +24h JWT.
    sleep 18
    recovered=0
    for attempt in 1 2 3; do
      "${PY}" "${RECOVER}" >> "${LOG}" 2>&1 || true
      if is_alive; then recovered=1; break; fi
      log "  step 3 retry ${attempt}/3 — token not minted yet, waiting 6s"
      sleep 6
    done
    if [ "${recovered}" = "1" ]; then
      log "✓ step 3 succeeded — token alive (Chrome minted fresh JWT)"
      exit 0
    fi
  fi
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
