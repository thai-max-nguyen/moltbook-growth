#!/usr/bin/env python3
"""Reddit growth monitor — watch karma growth + self-improve (2026-06-08).

Mirror of mundo_growth_monitor for the triathlete reddit account. Daily:
robust karma fetch (retry; dead token = abort, not blind action), accumulate a
snapshot, compute rolling rates, and apply ONE bounded auto-tune: POSTS_PER_DAY
in [1,3] via reddit_growth_config.json (which reddit_post reads at runtime).

Tune logic keys off whether posts actually EARN karma:
- link_karma climbing  → posts land → can post more (raise, max 3).
- link_karma flat/neg  → posts not landing (removed / downvoted / too-AI / wrong
  subs) → post less (lower, min 1) + flag to investigate. Avoids ban risk from
  spamming posts that don't stick.

Also flags the profile-only gate (total karma < 50 = posts auto-removed in real
subs; only the profile sub works until then).

Cron: 30 22 * * *  (after the moltbook monitor 22:15).
"""
import json
import time
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests

DATA_DIR = Path.home() / ".config" / "mundo-bot"
STATS_FILE = DATA_DIR / "reddit_stats.json"
CONFIG_FILE = DATA_DIR / "reddit_growth_config.json"
LEARNINGS_FILE = DATA_DIR / "reddit_learnings.md"

KARMA_GATE = 50              # below this, real subs auto-remove posts (profile-only)
POSTS_PER_DAY_DEFAULT = 2


def log(m):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {m}")


def _auth_headers():
    """Reuse reddit_post's OAuth config + header builder (no preflight)."""
    import reddit_post as rp
    cfg = rp.load_config()
    return rp.get_headers(cfg)


def fetch_karma(retries=3, gap=5):
    try:
        headers = _auth_headers()
    except Exception as e:
        log(f"auth/config load failed: {e}")
        return None
    for i in range(retries):
        try:
            r = requests.get("https://oauth.reddit.com/api/v1/me", headers=headers, timeout=12)
            if r.status_code == 401:
                log("401 — reddit token dead; abort (re-login at reddit.com in Chrome)")
                return None
            if r.ok:
                d = r.json()
                lk, ck = d.get("link_karma", 0), d.get("comment_karma", 0)
                return {"link_karma": lk, "comment_karma": ck, "total_karma": lk + ck}
        except Exception as e:
            log(f"fetch attempt {i+1}/{retries} failed: {e}")
        if i < retries - 1:
            time.sleep(gap)
    return None


def _parse_ts(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def _rate(snaps, key, days):
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
            anchor = anchor or s
    if not anchor or anchor.get(key) is None or now.get(key) is None:
        return None
    dd = (now_ts - _parse_ts(anchor["ts"])).total_seconds() / 86400
    if dd < 0.5:
        return None
    return round((now[key] - anchor[key]) / dd, 2)


def load_config():
    try:
        return json.load(open(CONFIG_FILE))
    except Exception:
        return {"posts_per_day": POSTS_PER_DAY_DEFAULT}


def main():
    live = fetch_karma()
    if not live:
        log("✗ no karma data (dead token / network) — abort, no blind tune")
        return 1

    stats = json.load(open(STATS_FILE)) if STATS_FILE.exists() else {"snapshots": []}
    now_iso = datetime.now(timezone.utc).isoformat()
    stats.setdefault("snapshots", []).append({"ts": now_iso, **live})
    snaps = stats["snapshots"]

    td7 = _rate(snaps, "total_karma", 7)
    ld7 = _rate(snaps, "link_karma", 7)
    cd7 = _rate(snaps, "comment_karma", 7)
    log(f"karma total={live['total_karma']} (link={live['link_karma']} comment={live['comment_karma']}) | "
        f"per-day 7d: total={td7} link(posts)={ld7} comment={cd7}")

    cfg = load_config()
    ppd = max(1, min(4, int(cfg.get("posts_per_day", POSTS_PER_DAY_DEFAULT))))
    mc = max(3, min(10, int(cfg.get("max_comments", 6))))
    actions = []

    # ── COMMENT cap tune (the escape lever, esp. while gated) — key off
    #    comment_karma/day. Landing → push higher; flat/removed → back off.
    if cd7 is not None:
        if cd7 >= 8 and mc < 10:
            mc += 1; actions.append(f"max_comments → {mc} (comments landing, push escape)")
        elif cd7 < 3 and mc > 3:
            mc -= 1; actions.append(f"max_comments → {mc} (comments not converting — removed/downvoted/too-AI?)")

    # ── POST cap tune — gate-aware. While gated, posts mostly hit the no-mod
    #    profile sub (safe but low reach); keep modest. Ungated → scale by link.
    if live["total_karma"] < KARMA_GATE:
        verdict = (f"profile-GATED: total karma {live['total_karma']} < {KARMA_GATE} — real-sub posts "
                   f"auto-removed; COMMENTS are the way out (max_comments={mc})")
    elif ld7 is not None:
        if ld7 <= 0 and ppd > 1:
            ppd -= 1; actions.append(f"posts_per_day → {ppd} (posts not landing, investigate)")
            verdict = f"posts NOT landing (link_karma/day {ld7})"
        elif ld7 >= 2.0 and ppd < 4:
            ppd += 1; actions.append(f"posts_per_day → {ppd} (lean in)")
            verdict = f"posts landing well (link_karma/day {ld7})"
        else:
            verdict = f"steady (link/day {ld7}, comment/day {cd7})"
    else:
        verdict = f"steady (comment/day {cd7})"

    cfg["posts_per_day"] = ppd
    cfg["max_comments"] = mc
    cfg["_updated"] = now_iso
    cfg["_verdict"] = verdict
    json.dump(cfg, open(CONFIG_FILE, "w"), indent=2)
    action = "; ".join(actions) if actions else None
    log(f"experiment: {verdict}" + (f" · {action}" if action else ""))

    stats["snapshots"] = snaps[-200:]
    stats["growth_rate_latest"] = {"ts": now_iso, "total_karma_per_day_7d": td7,
                                   "link_karma_per_day_7d": ld7, "comment_karma_per_day_7d": cd7,
                                   "verdict": verdict, "posts_per_day": ppd, "max_comments": mc}
    json.dump(stats, open(STATS_FILE, "w"), indent=2)
    try:
        with open(LEARNINGS_FILE, "a") as f:
            f.write(f"\n### Reddit growth pulse {datetime.now().strftime('%Y-%m-%d')}\n"
                    f"- total karma **{live['total_karma']}** · per-day(7d) total {td7}, "
                    f"link(posts) {ld7}, comment {cd7}.\n- {verdict}"
                    + (f" → {action}" if action else "") + "\n")
    except Exception as e:
        log(f"learnings append failed: {e}")
    log("=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
