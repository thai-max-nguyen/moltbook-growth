#!/usr/bin/env python3
"""Wrapper for launchd-driven engage cron.

Replaces the brittle vixie-cron `0 */2` line with a launchd-controlled equivalent
that survives macOS sleep-resume (vixie-cron silent fails after sleep).

Chain: refresh_token → engage → update catchup_state.last_engage_date.
"""
import json
import os
import subprocess
import sys
from datetime import date

CONFIG_DIR = os.path.expanduser("~/.config/mundo-bot")
PYTHON = "/usr/bin/python3"


def run(script_name):
    path = os.path.join(CONFIG_DIR, script_name)
    return subprocess.run([PYTHON, path], capture_output=False, check=False).returncode


def update_engage_state():
    f = os.path.join(CONFIG_DIR, "catchup_state.json")
    try:
        d = json.load(open(f)) if os.path.exists(f) else {}
    except (json.JSONDecodeError, OSError):
        d = {}
    d["last_engage_date"] = date.today().isoformat()
    json.dump(d, open(f, "w"))


def main():
    rc = run("refresh_token.py")
    if rc != 0:
        print(f"refresh_token.py exit {rc} — proceed anyway (cached token may work)")
    rc = run("mundo_engage.py")
    if rc == 0:
        update_engage_state()
    sys.exit(rc)


if __name__ == "__main__":
    main()
