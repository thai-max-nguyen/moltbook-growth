#!/usr/bin/env python3
"""Garmin watch-sync health check.

Detects when watch hasn't synced overnight sleep by 09:00 ICT. Common cause:
watch not worn, watch not in Bluetooth range of phone, Garmin Connect app not
opened. Without sleep data, Health Profile shows blank rows + downstream
ACWR/recovery metrics drift.

Behavior:
- Run via cron at 09:30 ICT daily.
- Pull /api/health-trends from local dashboard.
- If today's sleep_hours == None AND we're past 09:00, write alert flag in
  Health Profile + macOS notification.
- Exit 0 always (advisory only — does not block other crons).
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import urllib.request
import urllib.error

DASHBOARD = "http://localhost:8765/api/health-trends"
VAULT_FLAG = Path.home() / "Documents" / "Claude Second Brain" / "02 - User Profile" / "Max - Health Profile.md"


def fetch_trends():
    try:
        with urllib.request.urlopen(DASHBOARD, timeout=5) as r:
            return json.load(r)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None


def update_vault_flag(message):
    if not VAULT_FLAG.exists():
        return
    content = VAULT_FLAG.read_text()
    flag_marker = "<!-- garmin-sync-status -->"
    new_line = f'{flag_marker} **Garmin sync**: {message}\n'
    lines = content.split("\n")
    out = []
    replaced = False
    for line in lines:
        if flag_marker in line:
            out.append(new_line.rstrip())
            replaced = True
        else:
            out.append(line)
    if not replaced:
        for i, line in enumerate(out):
            if line.startswith("## LIVE SNAPSHOT"):
                out.insert(i + 2, new_line.rstrip())
                break
    VAULT_FLAG.write_text("\n".join(out))


def notify(title, message):
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def main():
    now = datetime.now()
    today_iso = now.date().isoformat()

    if now.hour < 9:
        print(f"garmin-sync-check: too early ({now.strftime('%H:%M')}) — skip")
        return

    data = fetch_trends()
    if not data:
        print("garmin-sync-check: dashboard unreachable — skip", file=sys.stderr)
        return

    dates = data.get("dates", [])
    sleep = data.get("sleep_hours", [])
    if not dates or not sleep:
        print("garmin-sync-check: empty trends data", file=sys.stderr)
        return

    # Find today's row
    today_idx = None
    for i, d in enumerate(dates):
        if d == today_iso:
            today_idx = i
            break

    if today_idx is None:
        msg = f"⚠️ no row for {today_iso} in dashboard — sync may have stalled"
        update_vault_flag(msg)
        notify("Garmin sync gap", "No row for today — check Garmin Connect app")
        print(f"garmin-sync-check: {msg}", file=sys.stderr)
        return

    today_sleep = sleep[today_idx]
    if today_sleep is None:
        msg = f"⚠️ no sleep data for {today_iso} as of {now.strftime('%H:%M')} — open Garmin Connect to force sync"
        update_vault_flag(msg)
        notify("Garmin sleep missing", f"No sleep data for {today_iso} — open Garmin Connect")
        print(f"garmin-sync-check: {msg}", file=sys.stderr)
    else:
        update_vault_flag(f"OK 🟢 ({today_sleep}h slept, synced)")
        print(f"garmin-sync-check: OK ({today_sleep}h)")


if __name__ == "__main__":
    main()
