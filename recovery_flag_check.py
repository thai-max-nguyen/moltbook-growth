#!/usr/bin/env python3
"""Recovery-mode flag check at 06:00 ICT.

Pulls Garmin Body Battery AM + RHR + sleep score from local dashboard.
Writes recovery_mode flag to ~/.config/mundo-bot/recovery_state.json that
other crons read to throttle aggressive activity.

Triggers (any one):
- Body Battery AM < 60 (low recharge)
- RHR > baseline (44-49) + 5 bpm = 50+ (autonomic load)
- Sleep score < 60 (poor recovery)

Output flag consumed by:
- morning_workflow.py: shifts day_mode to RECOVERY, suggests light workout
- mundo engage cycles: skip captcha-heavy retries (network respect)
- 22:30 wind-down notification: stronger nudge if 2+ recovery days in row
"""
import json
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

DASHBOARD = "http://localhost:8765/api/health-trends"
FLAG_FILE = Path.home() / ".config" / "mundo-bot" / "recovery_state.json"


def fetch_today():
    try:
        with urllib.request.urlopen(DASHBOARD, timeout=5) as r:
            d = json.loads(r.read())
    except Exception as e:
        print(f"recovery-check: dashboard unreachable: {e}", file=sys.stderr)
        return None

    today = datetime.now().date().isoformat()
    dates = d.get("dates", [])
    if today not in dates:
        return None
    i = dates.index(today)
    return {
        "bb_am": d.get("body_battery_morning", [None])[i],
        "rhr": d.get("resting_hr", [None])[i],
        "sleep_hours": d.get("sleep_hours", [None])[i],
        "sleep_score": d.get("sleep_score", [None])[i],
        "stress_avg": d.get("stress_avg", [None])[i],
    }


def evaluate(metrics):
    if metrics is None:
        return False, "no data"
    triggers = []
    bb = metrics.get("bb_am")
    rhr = metrics.get("rhr")
    score = metrics.get("sleep_score")
    if bb is not None and bb < 60:
        triggers.append(f"BB AM {bb} <60")
    if rhr is not None and rhr > 49:
        triggers.append(f"RHR {rhr} >49")
    if score is not None and score < 60:
        triggers.append(f"sleep score {score} <60")
    return bool(triggers), " | ".join(triggers) or "all metrics ok"


def write_flag(active, reason, metrics):
    state = {
        "ts": datetime.now().isoformat(),
        "recovery_mode": active,
        "reason": reason,
        "metrics": metrics,
    }
    FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
    FLAG_FILE.write_text(json.dumps(state, indent=2))
    print(f"recovery_mode={active} | {reason}")


def main():
    metrics = fetch_today()
    active, reason = evaluate(metrics)
    write_flag(active, reason, metrics)


if __name__ == "__main__":
    main()
