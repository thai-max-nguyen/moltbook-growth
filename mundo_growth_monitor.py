#!/usr/bin/env python3
"""Mundo growth monitor — watch the growth RATE and self-improve (2026-06-08).

Closes the loop the existing tools leave open:
- mundo_daily_review = recommend-only (no auto-action) + dies on asleep nights.
- mundo_ab_closer    = pillar A/B only.

This runs daily, robustly (retry the stats fetch so an asleep/network blip
doesn't blank the day), accumulates a snapshot, computes rolling growth rates,
evaluates the active follower-conversion experiment, and applies ONE bounded,
reversible auto-tune: MAX_FOLLOWS in [2,6] via growth_config.json (which
mundo_engage reads at runtime). Slow + logged so it can't spiral.

Cron: 0 22 * * *  (after daily_review 21:45; independent retry path).
"""
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".config" / "mundo-bot"
STATS_FILE = DATA_DIR / "mundo_stats.json"
CONFIG_FILE = DATA_DIR / "growth_config.json"
LEARNINGS_FILE = DATA_DIR / "mundo_learnings.md"

_envf = DATA_DIR / ".env"
import os
if _envf.exists():
    for _l in _envf.read_text().splitlines():
        if "=" in _l and not _l.lstrip().startswith("#"):
            k, v = _l.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
API_KEY = os.environ.get("MOLTBOOK_API_KEY", "")
BASE = "https://www.moltbook.com/api/v1"

# Follower-conversion experiment deployed 2026-06-08; pre-deploy baseline.
EXPERIMENT_START = "2026-06-08"
FOLLOWERS_PER_DAY_BASELINE = 1.11
FOLLOWERS_PER_DAY_TARGET = 2.0


def log(m):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {m}")


def fetch_live_stats(retries=3, gap=5):
    if not API_KEY:
        log("no API key — skip")
        return None
    for i in range(retries):
        try:
            req = urllib.request.Request(f"{BASE}/agents/me",
                                         headers={"Authorization": f"Bearer {API_KEY}",
                                                  "User-Agent": "mundo-monitor/1.0"})
            data = json.loads(urllib.request.urlopen(req, timeout=12).read())
            p = data.get("agent", {})
            return {"karma": p.get("karma", 0), "followers": p.get("follower_count", 0),
                    "posts": p.get("posts_count", 0), "comments": p.get("comments_count", 0)}
        except Exception as e:
            log(f"stats fetch attempt {i+1}/{retries} failed: {e}")
            if i < retries - 1:
                time.sleep(gap)
    return None


def _parse_ts(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        # older snapshots are tz-naive; treat as UTC so arithmetic is consistent
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def _rate(snaps, key, days):
    """Per-day rate of `key` over the last ~`days`, using the oldest snapshot
    within the window as the anchor. None if not enough history."""
    if not snaps:
        return None
    now = snaps[-1]
    now_ts = _parse_ts(now["ts"])
    if not now_ts:
        return None
    anchor = None
    for s in snaps[:-1]:
        ts = _parse_ts(s["ts"])
        if ts and 0 < (now_ts - ts).total_seconds() <= days * 86400 * 1.2:
            anchor = anchor or s  # oldest in window
    if not anchor or anchor.get(key) is None or now.get(key) is None:
        return None
    dt_days = (now_ts - _parse_ts(anchor["ts"])).total_seconds() / 86400
    if dt_days < 0.5:
        return None
    return round((now[key] - anchor[key]) / dt_days, 2)


def load_config():
    try:
        return json.load(open(CONFIG_FILE))
    except Exception:
        return {"max_follows": 3}


def main():
    stats = json.load(open(STATS_FILE)) if STATS_FILE.exists() else {"snapshots": []}
    live = fetch_live_stats()
    if not live:
        log("✗ could not fetch live stats after retries — abort (no blind action)")
        return 1

    now_iso = datetime.now(timezone.utc).isoformat()
    stats.setdefault("snapshots", []).append({"ts": now_iso, **live, "note": "growth_monitor"})
    snaps = stats["snapshots"]

    kd1, kd7 = _rate(snaps, "karma", 1), _rate(snaps, "karma", 7)
    fd1, fd7 = _rate(snaps, "followers", 1), _rate(snaps, "followers", 7)
    cd7 = _rate(snaps, "comments", 7)
    log(f"karma/day 1d={kd1} 7d={kd7} | followers/day 1d={fd1} 7d={fd7} | comments/day 7d={cd7}")

    # ── Evaluate follower experiment + bounded auto-tune of MAX_FOLLOWS ──
    cfg = load_config()
    mf = max(2, min(6, int(cfg.get("max_follows", 3))))
    verdict, action = "monitoring", None
    if fd7 is not None:
        if fd7 >= FOLLOWERS_PER_DAY_TARGET and mf < 6:
            mf += 1; verdict = f"WIN followers/day {fd7} ≥ {FOLLOWERS_PER_DAY_TARGET}"
            action = f"MAX_FOLLOWS → {mf} (lean in)"
        elif fd7 < 1.0 and mf > 3:
            mf -= 1; verdict = f"weak followers/day {fd7} < 1.0"
            action = f"MAX_FOLLOWS → {mf} (back off)"
        elif fd7 < 1.0:
            verdict = f"STALL followers/day {fd7} < 1.0 — follow lever not converting; pivot to POST quality"
        else:
            verdict = f"steady followers/day {fd7}"
    cfg["max_follows"] = mf
    cfg["_updated"] = now_iso
    cfg["_verdict"] = verdict
    json.dump(cfg, open(CONFIG_FILE, "w"), indent=2)
    log(f"experiment: {verdict}" + (f" · {action}" if action else ""))

    # persist snapshot (cap history) + append pulse to vault-synced learnings
    stats["snapshots"] = snaps[-200:]
    stats["growth_rate_latest"] = {"ts": now_iso, "karma_per_day_7d": kd7,
                                   "followers_per_day_7d": fd7, "comments_per_day_7d": cd7,
                                   "verdict": verdict, "max_follows": mf}
    json.dump(stats, open(STATS_FILE, "w"), indent=2)
    try:
        with open(LEARNINGS_FILE, "a") as f:
            f.write(f"\n### Growth pulse {datetime.now().strftime('%Y-%m-%d')}\n"
                    f"- karma/day(7d) **{kd7}**, followers/day(7d) **{fd7}** "
                    f"(target {FOLLOWERS_PER_DAY_TARGET}), comments/day(7d) {cd7}.\n"
                    f"- experiment: {verdict}" + (f" → {action}" if action else "") + "\n")
    except Exception as e:
        log(f"learnings append failed: {e}")
    log("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
