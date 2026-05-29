#!/usr/bin/env python3
"""Pre-flight Moltbook API key validator + auto-rotator.

Failure mode (2026-05-28): credentials.json held a stale key for ~weeks
while .env held the live key. mundo_engage.py reads from MOLTBOOK_API_KEY
env (sourced from .env) so it kept working — but ANY script reading
credentials.json (or human debugging) saw 401. This script makes
credentials.json self-healing.

Logic:
  1. Read key from credentials.json. Probe /agents/me.
  2. If 200 → exit 0 (silent).
  3. If 401 → read fallback key from .env (MOLTBOOK_API_KEY).
     - If .env key works → sync to credentials.json + log rotation event.
     - If .env also fails → write CRIT alert to alert log + non-zero exit.

Wire into crontab BEFORE refresh_token.py on engage + daily_post lines.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CREDS_JSON = HOME / ".config/moltbook/credentials.json"
ENV_FILE = HOME / ".config/mundo-bot/.env"
LOG = HOME / "Library/Logs/mundo-bot/key_check.log"
ALERT = HOME / "Library/Logs/mundo-bot/key_alert.CRIT"
PROBE_URL = "https://www.moltbook.com/api/v1/agents/me"

LOG.parent.mkdir(parents=True, exist_ok=True)


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {msg}\n")
    print(msg)


def _probe(key, timeout=10):
    """Return True if key authenticates, False if explicit 401, None if uncertain (network/DNS error).

    The None case is critical: cron fires through wifi-sleep DNS hiccups
    (gaierror). Treating those as "key failed" caused false-CRIT alerts on
    2026-05-29 — DNS came back fine 30s later, key was always valid.
    Callers must NOT rotate or raise on None — only on False.
    """
    req = urllib.request.Request(
        PROBE_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": "mundo-key-check/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200:
                body = json.loads(r.read())
                return body.get("success") is True
            return False
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False
        _log(f"probe HTTPError {e.code}: {e.reason} — treating as uncertain")
        return None
    except Exception as e:
        _log(f"probe network/uncertain: {e!r}")
        return None


def _read_creds_key():
    if not CREDS_JSON.exists():
        return None
    try:
        return json.loads(CREDS_JSON.read_text()).get("api_key")
    except Exception as e:
        _log(f"credentials.json unreadable: {e!r}")
        return None


def _read_env_key():
    if not ENV_FILE.exists():
        return None
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "MOLTBOOK_API_KEY":
                return v.strip().strip('"').strip("'")
    except Exception as e:
        _log(f".env unreadable: {e!r}")
    return None


def _sync_creds(new_key, agent_name="mundo"):
    """Replace credentials.json key. Keeps file shape minimal."""
    payload = {"api_key": new_key, "agent_name": agent_name}
    CREDS_JSON.parent.mkdir(parents=True, exist_ok=True)
    CREDS_JSON.write_text(json.dumps(payload) + "\n")


def _raise_crit(msg):
    """Persist a CRIT marker file so morning recap surfaces it."""
    ts = datetime.now().isoformat(timespec="seconds")
    ALERT.write_text(f"[{ts}] {msg}\n")
    _log(f"CRIT — {msg}")


def main():
    creds_key = _read_creds_key()
    creds_result = _probe(creds_key) if creds_key else False
    if creds_result is True:
        return 0
    if creds_result is None:
        _log("credentials.json probe inconclusive (network) — skipping rotation; will retry next cron")
        return 0  # Don't raise CRIT on network blips.

    _log(f"credentials.json key failed probe (key={(creds_key or '')[:18]}…). Trying .env fallback.")
    env_key = _read_env_key()
    if env_key and env_key != creds_key:
        env_result = _probe(env_key)
        if env_result is True:
            _sync_creds(env_key)
            _log(f"ROTATED credentials.json ← .env (new key {env_key[:18]}…). Removing stale alert.")
            if ALERT.exists():
                ALERT.unlink()
            return 0
        if env_result is None:
            _log(".env probe inconclusive too — skipping CRIT, retry next cron")
            return 0

    _raise_crit(
        "Both credentials.json AND .env Moltbook keys fail 401. Engage + daily_post will fail. "
        "Action: re-issue key at https://www.moltbook.com/skill.md (auth via mundo creator), "
        "write to ~/.config/mundo-bot/.env AND ~/.config/moltbook/credentials.json."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
