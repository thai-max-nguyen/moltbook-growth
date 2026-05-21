#!/bin/bash
# growth_cycle.sh — mundo + reddit growth loop, launchd-fired, self-stopping
#
# State:  /tmp/loop_growth_state.json    (cycle, consecutive_dead, karma snapshot)
# Log:    ~/Library/Logs/mundo-bot/growth_loop.log
# Plist:  ~/Library/LaunchAgents/com.mundo.growth.plist
#
# Stop conditions: cycle >= MAX_CYCLES (12)  OR  consecutive_dead >= MAX_DEAD (3)
# On stop: unloads own launchd agent + deletes own plist.
#
# Designed to inherit FDA when launchd loaded from Terminal.app (which has FDA).
# Do NOT load via cron — cron lost FDA per feedback_cron_recovery memory.

set -uo pipefail

# ---- config ----
STATE=/tmp/loop_growth_state.json
LOG="$HOME/Library/Logs/mundo-bot/growth_loop.log"
PLIST="$HOME/Library/LaunchAgents/com.mundo.growth.plist"
PLIST_LABEL=com.mundo.growth
MAX_CYCLES=48
MAX_DEAD=6
ENGAGE_TIMEOUT=1500   # 25min — engage typically 17min, give headroom

export USER=lap15964
export HOME=/Users/lap15964
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin

PY=/usr/bin/python3
JQ=/opt/homebrew/bin/jq

mkdir -p "$(dirname "$LOG")"

# ---- helpers ----
json_get() {
  # $1=file $2=key, prints value or empty
  [ -f "$1" ] && $JQ -r "$2 // empty" "$1" 2>/dev/null
}

run_with_timeout() {
  # $1=seconds, $2..=cmd. Returns exit of cmd, or 124 if timeout, or 137 if killed.
  # 2026-05-21 fix H: hung engage 70120 survived TERM+KILL for 50 min during
  # auth-recovery flapping. Add pkill -f backup that nukes ANY mundo_engage.py
  # processes after the per-pid kill. Then `wait` returns even on orphans.
  local secs=$1; shift
  local cmdname=$(basename "${2:-}")
  "$@" &
  local pid=$!
  (
    sleep "$secs"
    kill -TERM "$pid" 2>/dev/null
    sleep 5
    kill -KILL "$pid" 2>/dev/null
    # Belt-and-suspenders: kill any orphaned children matching cmd
    [ -n "$cmdname" ] && pkill -KILL -f "$cmdname" 2>/dev/null
  ) &
  local killer=$!
  wait "$pid" 2>/dev/null
  local rc=$?
  kill -KILL "$killer" 2>/dev/null
  wait "$killer" 2>/dev/null
  return $rc
}

stop_self() {
  local reason="$1"
  echo "$(date '+%Y-%m-%d %H:%M:%S') cycle=$CYCLE STOP $reason" >> "$LOG"
  /bin/launchctl unload "$PLIST" 2>/dev/null
  rm -f "$PLIST"
  exit 0
}

# ---- 2026-05-21 fix J: nuke orphan engages from previous fire ----
# When launchd kickstart -k SIGTERMs growth_cycle, the killer subshell dies
# with parent → engage child is orphaned and survives forever. On each new
# fire, kill any mundo_engage.py older than 25min (=ENGAGE_TIMEOUT). Also
# kill stale claude --print procs.
for stale_pid in $(pgrep -f "mundo_engage.py"); do
  age_s=$(ps -o etime= -p "$stale_pid" 2>/dev/null | awk -F: '{
    if (NF==3) print ($1*3600)+($2*60)+$3
    else if (NF==2) print ($1*60)+$2
    else print 0
  }')
  if [ "${age_s:-0}" -gt 1500 ]; then
    kill -KILL "$stale_pid" 2>/dev/null
  fi
done
# Stale claude --print (>5min)
for stale_pid in $(pgrep -f "claude --print"); do
  age_s=$(ps -o etime= -p "$stale_pid" 2>/dev/null | awk -F: '{
    if (NF==3) print ($1*3600)+($2*60)+$3
    else if (NF==2) print ($1*60)+$2
    else print 0
  }')
  if [ "${age_s:-0}" -gt 300 ]; then
    kill -KILL "$stale_pid" 2>/dev/null
  fi
done

# ---- load state ----
if [ -f "$STATE" ]; then
  CYCLE=$(json_get "$STATE" '.cycle')
  DEAD=$(json_get "$STATE" '.consecutive_dead')
  PREV_KARMA=$(json_get "$STATE" '.karma')
else
  CYCLE=0; DEAD=0; PREV_KARMA=0
fi
CYCLE=${CYCLE:-0}
DEAD=${DEAD:-0}
PREV_KARMA=${PREV_KARMA:-0}

CYCLE=$((CYCLE + 1))
TS=$(date '+%Y-%m-%d %H:%M:%S')
ACTIONS=""

# ---- pre-cycle stop guard ----
if [ "$CYCLE" -gt "$MAX_CYCLES" ]; then
  stop_self "max_cycles_already_hit"
fi

# ---- step 1: lock check ----
LOCK="$HOME/.config/mundo-bot/.engage.lock"
if [ -f "$LOCK" ]; then
  MTIME=$(stat -f %m "$LOCK")
  AGE=$(( ($(date +%s) - MTIME) / 60 ))
  PS=$(pgrep -f mundo_engage | head -1)
  if [ "$AGE" -gt 60 ] && [ -z "$PS" ]; then
    rm -f "$LOCK"
    ACTIONS="${ACTIONS}lock_stale_cleared "
  else
    ACTIONS="${ACTIONS}lock_kept "
  fi
fi

# ---- step 2: refresh token ----
if "$PY" "$HOME/.config/mundo-bot/refresh_token.py" >/dev/null 2>&1; then
  ACTIONS="${ACTIONS}refresh_ok "
else
  ACTIONS="${ACTIONS}refresh_FAIL "
fi

# ---- step 3: engage (with timeout + dead detection) ----
ENGAGE_TMP=$(mktemp /tmp/mundo_engage.XXXXXX)
run_with_timeout "$ENGAGE_TIMEOUT" "$PY" "$HOME/.config/mundo-bot/mundo_engage.py" > "$ENGAGE_TMP" 2>&1
ENGAGE_RC=$?
if grep -qiE "network dead|preflight abort|connection refused" "$ENGAGE_TMP"; then
  # Distinguish OUR connectivity death from an external moltbook outage.
  # If general internet is up, moltbook is down server-side — do NOT count
  # toward consecutive_dead self-stop, so the loop keeps probing and
  # auto-resumes when moltbook recovers (true "continuous").
  if curl -s -o /dev/null --max-time 5 https://www.google.com 2>/dev/null; then
    ACTIONS="${ACTIONS}moltbook_down(net_ok,no_self_stop) "
  else
    ACTIONS="${ACTIONS}engage_dead "
    DEAD=$((DEAD + 1))
  fi
elif [ "$ENGAGE_RC" -eq 143 ] || [ "$ENGAGE_RC" -eq 137 ] || [ "$ENGAGE_RC" -eq 124 ]; then
  # 2026-05-21: only count toward DEAD if engage did NO real work before timeout.
  # If ≥3 ✓ actions (reply/post_comment/upvote) landed, treat as partial_ok.
  # 2026-05-21: grep -c emits "0" on no-match AND exits 1 → "|| echo 0" then
  # piled a second "0" → "0\n0" in log. Drop fallback; grep -c is always numeric.
  PROGRESS=$(grep -cE "✓ reply|✓ post_comment|✓ comment|✓ upvote" "$ENGAGE_TMP" 2>/dev/null)
  PROGRESS=${PROGRESS:-0}
  if [ "$PROGRESS" -ge 3 ]; then
    ACTIONS="${ACTIONS}engage_partial_ok(${PROGRESS}) "
    DEAD=0
  else
    ACTIONS="${ACTIONS}engage_timeout(prog=${PROGRESS}) "
    DEAD=$((DEAD + 1))
  fi
elif [ "$ENGAGE_RC" -ne 0 ]; then
  ACTIONS="${ACTIONS}engage_err(rc=$ENGAGE_RC) "
  # err but not dead — don't increment DEAD
else
  ACTIONS="${ACTIONS}engage_ok "
  DEAD=0
fi
rm -f "$ENGAGE_TMP"

# ---- step 4: daily_post check ----
CATCHUP="$HOME/.config/mundo-bot/catchup_state.json"
if [ -f "$CATCHUP" ]; then
  LAST_POST_DATE=$(json_get "$CATCHUP" '.last_post_date')
  LAST_POST_TS=$(json_get "$CATCHUP" '.last_post_ts')
  TODAY=$(date +%Y-%m-%d)
  if [ "$LAST_POST_DATE" = "$TODAY" ]; then
    ACTIONS="${ACTIONS}daily_post_skip(today_done) "
  elif [ -n "$LAST_POST_TS" ]; then
    # parse ISO timestamp (drop fractional seconds), macOS-friendly
    BASE_TS="${LAST_POST_TS%.*}"
    LAST_EPOCH=$(date -j -f "%Y-%m-%dT%H:%M:%S" "$BASE_TS" +%s 2>/dev/null || echo 0)
    SINCE_MIN=$(( ($(date +%s) - LAST_EPOCH) / 60 ))
    if [ "$SINCE_MIN" -gt 30 ]; then
      run_with_timeout 300 "$PY" "$HOME/.config/mundo-bot/mundo_daily_post.py" >/dev/null 2>&1 \
        && ACTIONS="${ACTIONS}daily_post_ok " \
        || ACTIONS="${ACTIONS}daily_post_FAIL "
    else
      ACTIONS="${ACTIONS}daily_post_skip(${SINCE_MIN}min) "
    fi
  else
    ACTIONS="${ACTIONS}daily_post_no_state "
  fi
fi

# ---- step 5: reddit comment — disabled 2026-05-21 (script never created;
#                 was polluting logs with reddit_cmt_missing every cycle) ----

# ---- step 6: reddit post (window 9-22 ICT AND last post >4h) ----
# 2026-05-21: widened 15-22 → 9-22 (was skipping ~half of morning cycles)
HOUR=$(date +%H)
if [ "$HOUR" -ge 9 ] && [ "$HOUR" -le 22 ]; then
  REDDIT_LOG="$HOME/Library/Logs/mundo-bot/reddit_post.log"
  SHOULD_POST=1
  if [ -f "$REDDIT_LOG" ]; then
    AGE_H=$(( ($(date +%s) - $(stat -f %m "$REDDIT_LOG")) / 3600 ))
    if [ "$AGE_H" -le 4 ]; then
      SHOULD_POST=0
      ACTIONS="${ACTIONS}reddit_post_skip(age${AGE_H}h) "
    fi
  fi
  if [ "$SHOULD_POST" -eq 1 ]; then
    run_with_timeout 300 "$PY" "$HOME/.config/mundo-bot/reddit_post.py" >/dev/null 2>&1 \
      && ACTIONS="${ACTIONS}reddit_post_ok " \
      || ACTIONS="${ACTIONS}reddit_post_FAIL "
  fi
else
  ACTIONS="${ACTIONS}reddit_post_skip(hour=$HOUR) "
fi

# ---- step 7: stats via profile API ----
PROFILE=$(curl -s --max-time 5 https://www.moltbook.com/api/v1/agents/mundo/profile 2>/dev/null)
if [ -n "$PROFILE" ] && echo "$PROFILE" | $JQ -e . >/dev/null 2>&1; then
  KARMA=$(echo "$PROFILE" | $JQ -r '.karma // 0')
  FOLLOWERS=$(echo "$PROFILE" | $JQ -r '.follower_count // 0')
  POSTS=$(echo "$PROFILE" | $JQ -r '.posts_count // 0')
  COMMENTS=$(echo "$PROFILE" | $JQ -r '.comments_count // 0')
  KARMA_DELTA=$((KARMA - PREV_KARMA))
  # 2026-05-20 Bug3 guard: valid JSON shell but karma=0 while we had a real
  # prior karma == partial moltbook outage. Carry forward, do NOT persist 0
  # (else next recovery logs a fake ΔN spike that poisons stats/daily_review).
  if [ "$KARMA" -eq 0 ] && [ "$PREV_KARMA" -gt 0 ]; then
    KARMA=$PREV_KARMA; FOLLOWERS=0; POSTS=0; COMMENTS=0; KARMA_DELTA=0
    ACTIONS="${ACTIONS}stats_zero_outage(carry_fwd) "
  fi
else
  # 2026-05-21 fix E: carry forward ALL metrics on API fail (was wiping
  # foll/posts/comm to 0 → poisoned daily_review deltas). Read prev state.
  PREV_FOLL=$(json_get "$STATE" '.followers'); PREV_POSTS=$(json_get "$STATE" '.posts'); PREV_COMM=$(json_get "$STATE" '.comments')
  KARMA=$PREV_KARMA
  FOLLOWERS=${PREV_FOLL:-0}; POSTS=${PREV_POSTS:-0}; COMMENTS=${PREV_COMM:-0}
  KARMA_DELTA=0
  ACTIONS="${ACTIONS}stats_api_FAIL(carry_fwd) "
fi

# ---- step 7.5: continuous self-tune (guarded — no-op if API down / low sample) ----
OPT_OUT=$(run_with_timeout 30 "$PY" "$HOME/.config/mundo-bot/mundo_optimize.py" 2>&1 | tail -1)
ACTIONS="${ACTIONS}opt:[${OPT_OUT}] "

# ---- step 8: log + persist state ----
echo "$TS cycle=$CYCLE karma=$KARMA(Δ$KARMA_DELTA) foll=$FOLLOWERS posts=$POSTS comm=$COMMENTS dead=$DEAD | $ACTIONS" >> "$LOG"

cat > "$STATE" <<EOF
{"ts": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "cycle": $CYCLE, "consecutive_dead": $DEAD, "karma": $KARMA, "followers": $FOLLOWERS, "posts": $POSTS, "comments": $COMMENTS}
EOF

# ---- post-cycle stop check ----
if [ "$DEAD" -ge "$MAX_DEAD" ]; then
  stop_self "consecutive_dead=$DEAD"
fi
if [ "$CYCLE" -ge "$MAX_CYCLES" ]; then
  stop_self "max_cycles_reached"
fi

exit 0
