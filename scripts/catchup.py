#!/usr/bin/env python3
"""
mundo_catchup.py — runs on laptop wake/login via LaunchAgent.
Detects missed daily posts (cron skipped because laptop was off/no network)
and re-runs daily_post + engage if today's slot was missed.
Uses Haiku (same as cron scripts) — no extra cost.
"""
import os, json, subprocess, sys, time, socket
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

ICT = ZoneInfo("Asia/Ho_Chi_Minh")
DATA_DIR      = Path.home() / ".config/mundo-bot"
POSTED_FILE   = DATA_DIR / "posted_titles.json"
CATCHUP_FILE  = DATA_DIR / "catchup_state.json"
LOG_DIR       = Path.home() / "Library/Logs/mundo-bot"
LOG_FILE      = LOG_DIR / "catchup.log"
SCRIPT_DIR    = DATA_DIR
DAILY_SCRIPT  = SCRIPT_DIR / "mundo_daily_post.py"
ENGAGE_SCRIPT = SCRIPT_DIR / "mundo_engage.py"

LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg):
    ts = datetime.now(ICT).strftime("%Y-%m-%d %H:%M:%S ICT")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def has_network(host="www.moltbook.com", port=443, timeout=5) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except Exception:
        return False


def today_ict() -> str:
    return datetime.now(ICT).strftime("%Y-%m-%d")


def load_catchup_state() -> dict:
    if CATCHUP_FILE.exists():
        with open(CATCHUP_FILE) as f:
            return json.load(f)
    return {"last_post_date": "", "last_engage_date": ""}


def save_catchup_state(state: dict):
    with open(CATCHUP_FILE, "w") as f:
        json.dump(state, f, indent=2)


def already_posted_today() -> bool:
    """Only trust catchup_state.json — mtime fallback is unreliable (failed posts touch the file too)."""
    state = load_catchup_state()
    return state.get("last_post_date") == today_ict()


def already_engaged_today() -> bool:
    state = load_catchup_state()
    return state.get("last_engage_date") == today_ict()


def run_script(script: Path, label: str) -> bool:
    log(f"running {label}…")
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode == 0:
        log(f"{label} ✓")
        return True
    else:
        log(f"{label} ✗ rc={result.returncode}")
        if result.stderr:
            log(f"  stderr: {result.stderr.strip()[:200]}")
        return False


def main():
    log("catchup check started")

    # Wait for network — up to 60s after wake
    for attempt in range(12):
        if has_network():
            break
        log(f"no network — waiting 5s (attempt {attempt+1}/12)")
        time.sleep(5)
    else:
        log("no network after 60s — aborting")
        return

    log("network ok")
    state = load_catchup_state()
    today = today_ict()
    ran_anything = False

    if not already_posted_today():
        log(f"daily post missed for {today} — catching up")
        ok = run_script(DAILY_SCRIPT, "daily_post")
        if ok:
            state["last_post_date"] = today
            save_catchup_state(state)
            ran_anything = True
    else:
        log(f"daily post already done for {today} — skip")

    if not already_engaged_today():
        log(f"engage missed for {today} — catching up")
        ok = run_script(ENGAGE_SCRIPT, "engage")
        if ok:
            state["last_engage_date"] = today
            save_catchup_state(state)
            ran_anything = True
    else:
        log(f"engage already done for {today} — skip")

    if not ran_anything:
        log("nothing to catch up — all good")

    log("catchup check done")


if __name__ == "__main__":
    main()
