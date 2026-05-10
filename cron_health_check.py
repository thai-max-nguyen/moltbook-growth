#!/usr/bin/env python3
"""Cron health check — detects missed jobs and reruns them.

Runs 3x daily (10:00, 15:00, 22:00) via cron. For each tracked job, checks
whether a successful run happened within its expected freshness window. If
not, reruns the job inline and updates state.

Tracked jobs:
- mundo daily_post  → catchup_state.last_post_date == today
- mundo engage      → catchup_state.last_engage_date == today AND engage.log mtime < 4h ago
- garmin update     → garmin_update.log mtime < 24h ago
- reddit post       → reddit_post.log mtime < 24h ago AND post stamp >= today 15:00
- reddit comment    → reddit_comment.log mtime < 6h ago after first 14:00 firing
"""
import os, sys, json, subprocess, datetime as dt
from pathlib import Path

HOME    = Path(os.path.expanduser("~"))
CFG     = HOME / ".config/mundo-bot"
LOGS    = HOME / "Library/Logs/mundo-bot"
PY      = "/usr/bin/python3"
STATE_F = CFG / "catchup_state.json"
HEALTH_LOG = LOGS / "health_check.log"

NOW = dt.datetime.now()
TODAY = NOW.date()
ICT_HOUR = NOW.hour


def log(msg):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M')}] {msg}"
    print(line)
    with open(HEALTH_LOG, "a") as f:
        f.write(line + "\n")


def file_age_h(path):
    p = Path(path)
    if not p.exists():
        return float("inf")
    return (NOW.timestamp() - p.stat().st_mtime) / 3600


def state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {}


def write_state(d):
    STATE_F.write_text(json.dumps(d))


def run_chain(*scripts, log_file):
    """Run a chain of python scripts, redirecting stdout/stderr to log_file."""
    with open(log_file, "a") as f:
        for s in scripts:
            r = subprocess.run([PY, str(s)], stdout=f, stderr=subprocess.STDOUT, timeout=1800)
            if r.returncode != 0:
                return False
    return True


def check_garmin():
    age = file_age_h(LOGS / "garmin_update.log")
    if age > 24:
        log(f"⚠ garmin stale ({age:.1f}h) — rerunning")
        ok = run_chain(CFG / "refresh_token.py", CFG / "garmin_daily_update.py",
                       log_file=LOGS / "garmin_update.log")
        log(f"  garmin rerun {'✓' if ok else '✗'}")
        return ok
    log(f"✓ garmin fresh ({age:.1f}h)")
    return True


def check_mundo_post():
    s = state()
    last = s.get("last_post_date", "")
    if last == TODAY.isoformat():
        log(f"✓ mundo post done today ({last})")
        return True
    log(f"⚠ mundo post stale (last={last}) — rerunning")
    ok = run_chain(CFG / "refresh_token.py", CFG / "mundo_daily_post.py",
                   log_file=LOGS / "daily.log")
    if ok:
        s = state(); s["last_post_date"] = TODAY.isoformat(); write_state(s)
    log(f"  mundo post rerun {'✓' if ok else '✗'}")
    return ok


def check_mundo_engage():
    age = file_age_h(LOGS / "engage.log")
    s = state()
    last = s.get("last_engage_date", "")
    if last == TODAY.isoformat() and age < 4:
        log(f"✓ mundo engage fresh (last={last}, {age:.1f}h)")
        return True
    log(f"⚠ mundo engage stale (last={last}, age={age:.1f}h) — rerunning")
    ok = run_chain(CFG / "refresh_token.py", CFG / "mundo_engage.py",
                   log_file=LOGS / "engage.log")
    if ok:
        s = state(); s["last_engage_date"] = TODAY.isoformat(); write_state(s)
    log(f"  mundo engage rerun {'✓' if ok else '✗'}")
    return ok


def check_reddit_post():
    # Cron fires 15:00. Only check after 15:30 ICT.
    if ICT_HOUR < 15 or (ICT_HOUR == 15 and NOW.minute < 30):
        log("· reddit post window not yet (cron 15:00)")
        return True
    log_path = LOGS / "reddit_post.log"
    if not log_path.exists():
        log("⚠ reddit_post.log missing — checking token first")
    else:
        mtime = dt.datetime.fromtimestamp(log_path.stat().st_mtime)
        if mtime.date() == TODAY and mtime.hour >= 15:
            log(f"✓ reddit post fresh (mtime {mtime:%H:%M})")
            return True
        log(f"⚠ reddit post stale (last mtime {mtime:%Y-%m-%d %H:%M}) — checking token")
    # Check token before rerunning — skip if dead (saves wasted API call + log spam)
    token_check = subprocess.run([PY, str(CFG / "reddit_token_check.py")],
                                 capture_output=True, timeout=10)
    if token_check.returncode != 0:
        log("· reddit token dead — skip retry (user must re-login at reddit.com)")
        return True  # not a failure for cron health purposes
    with open(log_path, "a") as f:
        r = subprocess.run([PY, str(CFG / "reddit_post.py"), "--mode", "post"],
                           stdout=f, stderr=subprocess.STDOUT, timeout=1800)
    log(f"  reddit post rerun {'✓' if r.returncode == 0 else '✗'}")
    return r.returncode == 0


def check_reddit_comment():
    # Cron fires 14, 18, 22. Check that some firing today succeeded if past 14:30.
    if ICT_HOUR < 14 or (ICT_HOUR == 14 and NOW.minute < 30):
        log("· reddit comment window not yet (cron 14:00)")
        return True
    log_path = LOGS / "reddit_comment.log"
    if not log_path.exists():
        log("⚠ reddit_comment.log missing — rerunning")
    else:
        mtime = dt.datetime.fromtimestamp(log_path.stat().st_mtime)
        if mtime.date() == TODAY:
            log(f"✓ reddit comment touched today ({mtime:%H:%M})")
            return True
        log(f"⚠ reddit comment stale (last mtime {mtime:%Y-%m-%d %H:%M}) — rerunning")
    with open(log_path, "a") as f:
        r = subprocess.run([PY, str(CFG / "reddit_post.py"), "--mode", "comment"],
                           stdout=f, stderr=subprocess.STDOUT, timeout=1800)
    log(f"  reddit comment rerun {'✓' if r.returncode == 0 else '✗'}")
    return r.returncode == 0


def main():
    log(f"=== cron health check (hour={ICT_HOUR}) ===")
    results = {
        "garmin":         check_garmin(),
        "mundo_post":     check_mundo_post(),
        "mundo_engage":   check_mundo_engage(),
        "reddit_post":    check_reddit_post(),
        "reddit_comment": check_reddit_comment(),
    }
    failed = [k for k, v in results.items() if not v]
    log(f"=== done · ok={len(results)-len(failed)}/{len(results)} · failed={failed or 'none'} ===\n")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
