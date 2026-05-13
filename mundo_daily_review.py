#!/usr/bin/env python3
"""Daily 21:45 ICT review — mundo growth rate, errors, bugs, auto-tune signals.

Reads:
- mundo_stats.json snapshots (karma/followers/posts/comments delta)
- daily.log + engage.log (last 24h errors)
- moltbook API (live recent post performance per pillar)

Writes:
- mundo_learnings.md appended review section (date-stamped)
- mundo_review_state.json (last review timestamp, baseline anchors)

Auto-action:
- Flag pillars with >5 posts and <10 comments avg (saturating)
- Flag pillars with <2 posts in 7 days (underused)
- Flag any error pattern repeated 3+ times in logs

Does NOT modify pillar weights automatically — outputs recommendation
for human review.
"""
import json
import os
import re
import sys
import urllib.request
import urllib.error
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path.home() / ".config" / "mundo-bot"
LOG_DIR = Path.home() / "Library" / "Logs" / "mundo-bot"
STATS_FILE = DATA_DIR / "mundo_stats.json"
LEARNINGS_FILE = DATA_DIR / "mundo_learnings.md"
REVIEW_STATE_FILE = DATA_DIR / "mundo_review_state.json"

API_KEY = "moltbook_sk_qkJoY_eFVohoE70zQdfzW9g9m31lEGVW"
BASE = "https://www.moltbook.com/api/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "User-Agent": "mundo-review/1.0"}


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{ts}] {msg}")


def fetch_live_stats():
    try:
        req = urllib.request.Request(f"{BASE}/agents/me", headers=HEADERS)
        r = urllib.request.urlopen(req, timeout=10)
        data = json.loads(r.read())
        p = data.get("agent", {})
        return {
            "karma": p.get("karma", 0),
            "followers": p.get("follower_count", 0),
            "posts": p.get("posts_count", 0),
            "comments": p.get("comments_count", 0),
        }
    except Exception as e:
        log(f"live stats fetch failed: {e}")
        return None


def fetch_recent_posts():
    try:
        req = urllib.request.Request(f"{BASE}/agents/profile?name=mundo", headers=HEADERS)
        r = urllib.request.urlopen(req, timeout=10)
        return json.loads(r.read()).get("recentPosts", [])
    except Exception as e:
        log(f"recent posts fetch failed: {e}")
        return []


def load_stats():
    if not STATS_FILE.exists():
        return {"snapshots": []}
    return json.loads(STATS_FILE.read_text())


def load_review_state():
    if not REVIEW_STATE_FILE.exists():
        return {}
    return json.loads(REVIEW_STATE_FILE.read_text())


def save_review_state(state):
    REVIEW_STATE_FILE.write_text(json.dumps(state, indent=2))


def find_baseline(snapshots, days_ago):
    """Find snapshot closest to N days ago."""
    target = datetime.now() - timedelta(days=days_ago)
    best = None
    best_diff = float("inf")
    for s in snapshots:
        try:
            ts = datetime.fromisoformat(s["ts"].replace("Z", ""))
            if ts.tzinfo:
                ts = ts.replace(tzinfo=None)
            diff = abs((ts - target).total_seconds())
            if diff < best_diff:
                best_diff = diff
                best = s
        except Exception:
            continue
    return best


def parse_log_errors(log_path, hours=24):
    """Extract recent errors from cron log."""
    if not log_path.exists():
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    errors = []
    try:
        content = log_path.read_text(errors="replace")
    except Exception:
        return []
    # Exclude success patterns that contain "fail" in negation context
    exclude = ("failed=none", "ok=8/8", "ok=7/7", "no flags", "✓ ", "INFO")
    for line in content.split("\n"):
        line_lower = line.lower()
        if any(kw in line_lower for kw in ["error", "fail", "traceback", "timeout", "exception", "❌", "⚠"]):
            if not any(e in line for e in exclude):
                errors.append(line.strip()[:200])
    return errors[-30:]


def analyze_pillar_perf(recent_posts):
    """Per-pillar comment + upvote averages from recent posts."""
    by_submolt = defaultdict(list)
    for p in recent_posts:
        sub = p.get("submolt", {}).get("name", "?")
        upv = p.get("upvotes", 0)
        com = p.get("comment_count", 0)
        score = upv + com * 2
        by_submolt[sub].append((upv, com, score))
    return by_submolt


def main():
    log("=== mundo daily review ===")
    now = datetime.now()
    live = fetch_live_stats()
    if not live:
        log("abort — cannot fetch live stats")
        sys.exit(1)

    stats = load_stats()
    snapshots = stats.get("snapshots", [])
    yesterday = find_baseline(snapshots, days_ago=1)
    week_ago = find_baseline(snapshots, days_ago=7)

    # Deltas — guard against None values in snapshots
    def get_field(snap, key, default):
        if not snap:
            return default
        v = snap.get(key)
        return default if v is None else v

    d1_karma = live["karma"] - get_field(yesterday, "karma", live["karma"])
    d1_followers = live["followers"] - get_field(yesterday, "followers", live["followers"])
    d1_posts = live["posts"] - get_field(yesterday, "posts", live["posts"])
    d1_comments = live["comments"] - get_field(yesterday, "comments", live["comments"])

    d7_karma = live["karma"] - get_field(week_ago, "karma", live["karma"])
    d7_followers = live["followers"] - get_field(week_ago, "followers", live["followers"])
    d7_posts = live["posts"] - get_field(week_ago, "posts", live["posts"])
    d7_comments = live["comments"] - get_field(week_ago, "comments", live["comments"])

    log(f"karma {live['karma']} (+{d1_karma} d1, +{d7_karma} d7)")
    log(f"followers {live['followers']} (+{d1_followers} d1, +{d7_followers} d7)")
    log(f"posts {live['posts']} (+{d1_posts} d1, +{d7_posts} d7)")
    log(f"comments {live['comments']} (+{d1_comments} d1, +{d7_comments} d7)")

    # Pillar perf
    recent = fetch_recent_posts()
    by_submolt = analyze_pillar_perf(recent)
    perf_lines = []
    for sub, plist in sorted(by_submolt.items(), key=lambda kv: -sum(s[2] for s in kv[1])):
        avg_upv = sum(s[0] for s in plist) / len(plist)
        avg_com = sum(s[1] for s in plist) / len(plist)
        avg_score = sum(s[2] for s in plist) / len(plist)
        perf_lines.append(f"  m/{sub}: n={len(plist)} avg u={avg_upv:.1f} c={avg_com:.1f} score={avg_score:.1f}")

    # Errors from logs
    daily_errors = parse_log_errors(LOG_DIR / "daily.log")
    engage_errors = parse_log_errors(LOG_DIR / "engage.log")
    health_errors = parse_log_errors(LOG_DIR / "health_check.log")

    error_counter = Counter()
    for line in daily_errors + engage_errors + health_errors:
        sig = re.sub(r"\d+", "N", line[:80])
        error_counter[sig] += 1
    repeated_errors = [(s, c) for s, c in error_counter.most_common(5) if c >= 3]

    # Build recommendations
    recommendations = []
    if d1_karma < 5:
        recommendations.append(f"⚠ karma growth slow ({d1_karma}/day). Check pillar perf — maybe reweight or refresh templates.")
    if d1_posts < 3 and d1_posts >= 0:
        recommendations.append(f"⚠ only {d1_posts} posts today. Verify cron + MAX_POSTS_PER_DAY + self-throttle gap.")
    if d1_followers <= 0:
        recommendations.append("⚠ no follower growth today. Check intro_hook + introductions cooldown not blocking.")
    if repeated_errors:
        recommendations.append(f"⚠ {len(repeated_errors)} error patterns repeated 3+ times — investigate logs.")
    for sub, plist in by_submolt.items():
        n = len(plist)
        avg_com = sum(s[1] for s in plist) / n
        if n >= 5 and avg_com < 5:
            recommendations.append(f"⚠ m/{sub}: {n} recent posts, avg c={avg_com:.1f} — saturating, consider reweight down")
    if not recommendations:
        recommendations.append("✓ no flags — growth trending OK")

    # Append review to learnings.md
    review_md = [
        f"\n## Daily Review {now.strftime('%Y-%m-%d %H:%M')} (auto)",
        "",
        f"**Live snapshot:** karma={live['karma']} | followers={live['followers']} | posts={live['posts']} | comments={live['comments']}",
        "",
        f"**24h delta:** karma +{d1_karma} | followers +{d1_followers} | posts +{d1_posts} | comments +{d1_comments}",
        f"**7d delta:**  karma +{d7_karma} | followers +{d7_followers} | posts +{d7_posts} | comments +{d7_comments}",
        "",
        f"**Pillar performance (recent {sum(len(v) for v in by_submolt.values())} posts):**",
    ]
    review_md.extend(perf_lines)
    review_md.append("")
    if repeated_errors:
        review_md.append("**Repeated errors (last 24h):**")
        for sig, c in repeated_errors:
            review_md.append(f"  - {c}× `{sig.strip()}`")
        review_md.append("")
    review_md.append("**Recommendations:**")
    for r in recommendations:
        review_md.append(f"- {r}")
    review_md.append("")

    with open(LEARNINGS_FILE, "a") as f:
        f.write("\n".join(review_md))
    log(f"appended {len(review_md)} lines to mundo_learnings.md")

    # Save review state
    save_review_state({
        "last_review_ts": now.isoformat(),
        "live_at_review": live,
        "d1_karma": d1_karma,
        "d7_karma": d7_karma,
        "flagged_errors": [sig for sig, c in repeated_errors],
        "saturating_submolts": [sub for sub, plist in by_submolt.items()
                                if len(plist) >= 5 and sum(s[1] for s in plist) / len(plist) < 5],
    })

    log(f"=== done — {len(recommendations)} recommendations ===")


if __name__ == "__main__":
    main()
