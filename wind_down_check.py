#!/usr/bin/env python3
"""Wind-down notification at 22:30 ICT — fires only on sleep-debt accumulation.

Pulls last 2 nights of sleep_hours from local dashboard.
If avg < 6h: send macOS notification "wind-down for sleep recovery".
If avg >= 6h: silent (no nudge, no fatigue).

Designed to be a gentle nudge, not a strict block. User decides.
"""
import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta

DASHBOARD = "http://localhost:8765/api/health-trends"
SLEEP_DEBT_THRESHOLD_H = 6.0  # avg of last 2 nights below this triggers nudge


def fetch_recent_sleep(n_days=2):
    try:
        with urllib.request.urlopen(DASHBOARD, timeout=5) as r:
            d = json.loads(r.read())
    except Exception as e:
        print(f"wind-down: dashboard unreachable: {e}", file=sys.stderr)
        return []

    today = datetime.now().date()
    dates = d.get("dates", [])
    sleep_hours = d.get("sleep_hours", [])
    cutoff = today - timedelta(days=n_days)
    recent = []
    for i, dstr in enumerate(dates):
        try:
            dd = datetime.fromisoformat(dstr).date()
        except ValueError:
            continue
        if dd >= cutoff and sleep_hours[i] is not None:
            recent.append((dstr, sleep_hours[i]))
    return recent


def notify(title, message):
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
            check=False, timeout=5,
        )
    except Exception:
        pass


def main():
    recent = fetch_recent_sleep(n_days=2)
    if not recent:
        print("wind-down: no sleep data — skip")
        return
    avg = sum(s for _, s in recent) / len(recent)
    detail = " + ".join(f"{d}:{s}h" for d, s in recent)
    if avg < SLEEP_DEBT_THRESHOLD_H:
        msg = f"Avg {avg:.1f}h last {len(recent)} nights ({detail}). Bed soon."
        print(f"wind-down: NUDGE — {msg}")
        notify("🌙 Sleep debt accumulating", msg)
    else:
        print(f"wind-down: ok ({avg:.1f}h avg over {len(recent)} nights) — silent")


if __name__ == "__main__":
    main()
