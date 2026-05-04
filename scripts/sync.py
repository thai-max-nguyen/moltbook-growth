#!/usr/bin/env python3
"""mundo vault sync — runs weekly (Sunday 1:00 UTC).
Analyzes own post performance, generates actionable insights, saves to vault learnings."""
import os, json, time, subprocess, re, requests, warnings, sys
from datetime import datetime
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_auth import env_with_token  # noqa: E402

from config import get_api_key  # MOLTBOOK_API_KEY env or ~/.config/moltbook/credentials.json
API_KEY  = get_api_key()
BASE     = "https://www.moltbook.com/api/v1"
H        = {"Authorization": f"Bearer {API_KEY}"}

# Data to ~/.config/mundo-bot/ — cron can't write to ~/Documents/ (macOS TCC)
DATA_DIR       = os.path.expanduser("~/.config/mundo-bot")
os.makedirs(DATA_DIR, exist_ok=True)
STATS_FILE     = f"{DATA_DIR}/mundo_stats.json"
LEARNINGS_FILE = f"{DATA_DIR}/mundo_learnings.md"
CLAUDE_BIN     = "/Users/lap15964/.local/bin/claude"
# GitHub Pages stats file — written from local repo clone if available
PAGES_STATS    = os.path.expanduser("~/Documents/moltbook-growth/docs/stats.json")

def api_get(path, **kw):
    time.sleep(0.5)
    try:
        r = requests.get(f"{BASE}{path}", headers=H, timeout=15, **kw)
    except requests.exceptions.ConnectionError as e:
        print(f"[net-error] {path}: {e}")
        return {}
    return r.json() if r.ok else {}

_AUTH_ERRORS = ("not logged in", "please run /login", "authentication", "unauthorized")

def haiku(prompt):
    r = subprocess.run(
        [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", prompt[:2000]],
        capture_output=True, text=True, timeout=90, env=env_with_token()
    )
    out = r.stdout.strip()
    if any(e in out.lower() for e in _AUTH_ERRORS):
        return f"[auth error — check USER env in cron]"
    lines = out.split('\n')
    return '\n'.join(l for l in lines if not re.match(r'^[⚡🎯🧠].*\*\*', l)).strip()

def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE) as f:
            return json.load(f)
    return {"snapshots": [], "top_posts": []}

def save_stats(s):
    with open(STATS_FILE, "w") as f:
        json.dump(s, f, indent=2)

def load_learnings_tail(n=30):
    """Load last N lines of learnings for context injection."""
    if not os.path.exists(LEARNINGS_FILE):
        return ""
    with open(LEARNINGS_FILE) as f:
        lines = f.readlines()
    return ''.join(lines[-n:]).strip()

def fetch_own_posts():
    """Fetch mundo's recent posts with engagement stats."""
    data = api_get("/agents/profile", params={"name": "mundo"})
    posts = data.get("agent", {}).get("recent_posts") or data.get("posts", [])
    return posts

def analyze_post_performance(posts):
    """Score posts by engagement = upvotes*2 + comments. Return top 5."""
    scored = []
    for p in posts:
        score = p.get("upvotes", 0) * 2 + p.get("comment_count", 0)
        scored.append({
            "title":    p.get("title", ""),
            "submolt":  p.get("submolt", ""),
            "upvotes":  p.get("upvotes", 0),
            "comments": p.get("comment_count", 0),
            "score":    score,
            "length":   len(p.get("content", "")),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:5]

def check_shadow_ban(snapshots):
    """Detect silent suspension: 2+ consecutive weekly cycles with <2 karma growth and >10 posts."""
    if len(snapshots) < 3:
        return False
    recent = snapshots[-3:]
    karma_deltas = [recent[i]["karma"] - recent[i-1]["karma"] for i in range(1, 3)]
    if all(d < 2 for d in karma_deltas) and snapshots[-1]["posts"] > 10:
        return True
    return False


def alert_shadow_ban():
    """Desktop notification on macOS."""
    try:
        subprocess.run(
            ["osascript", "-e",
             'display notification "mundo may be shadow-suspended. Check Moltbook manually." '
             'with title "Shadow-ban alert" sound name "Basso"'],
            timeout=5
        )
    except Exception:
        pass


def track_pillar_performance(posts, snapshots):
    """7-day per-pillar (submolt) karma delta from post engagement scores."""
    if not posts:
        return {}
    submolt_scores = {}
    for p in posts:
        sub = p.get("submolt", "unknown")
        score = p.get("upvotes", 0) * 2 + p.get("comment_count", 0)
        submolt_scores[sub] = submolt_scores.get(sub, 0) + score

    if len(snapshots) >= 2:
        prev_snap = snapshots[-2]
        karma_delta = snapshots[-1]["karma"] - prev_snap["karma"]
    else:
        karma_delta = 0

    return {"by_submolt": submolt_scores, "total_karma_delta_7d": karma_delta}


def check_cron_health():
    """Read cron mail for errors, diagnose with Haiku, append to learnings."""
    mail_file = f"/var/mail/{os.environ.get('USER', 'lap15964')}"
    if not os.path.exists(mail_file):
        return
    try:
        with open(mail_file) as f:
            mail = f.read()
    except Exception:
        return

    # Look for cron errors from the last week
    error_lines = [l for l in mail.split('\n') if 'Operation not permitted' in l
                   or 'Error' in l or 'Traceback' in l or 'error' in l.lower()]
    if not error_lines:
        return

    unique_errors = list(dict.fromkeys(error_lines))[:10]
    diagnosis = haiku(
        f"These errors appeared in cron mail for the mundo Moltbook bot:\n\n"
        + '\n'.join(unique_errors) +
        "\n\nWrite a 2-sentence diagnosis: what caused this and how to fix it."
    )
    entry = f"\n### Cron Health Check — {datetime.now().strftime('%Y-%m-%d')}\n\n"
    entry += f"Errors detected:\n```\n" + '\n'.join(unique_errors[:5]) + "\n```\n\n"
    entry += f"Diagnosis: {diagnosis}\n"

    with open(LEARNINGS_FILE, "a") as f:
        f.write(entry)
    print(f"[health] logged {len(unique_errors)} cron errors to learnings")


def main():
    check_cron_health()
    profile = api_get("/agents/me").get("agent", {})
    snap = {
        "ts":       datetime.now().isoformat(),
        "karma":    profile.get("karma", 0),
        "followers": profile.get("follower_count", 0),
        "posts":    profile.get("posts_count", 0),
        "comments": profile.get("comments_count", 0),
    }
    print(f"Snapshot: {snap}")

    stats = load_stats()
    prev  = stats["snapshots"][-1] if stats["snapshots"] else snap

    karma_delta    = snap["karma"]    - prev["karma"]
    follower_delta = snap["followers"] - prev["followers"]

    # Fetch and analyze own post performance
    own_posts   = fetch_own_posts()
    top_posts   = analyze_post_performance(own_posts) if own_posts else []
    print(f"Top posts this week: {[p['title'][:40] for p in top_posts]}")

    # Shadow-ban detection (closes issue #2)
    stats["snapshots"].append(snap)  # add current before check so we have 3 points
    if check_shadow_ban(stats["snapshots"]):
        warn_entry = (
            f"\n### ⚠️ Shadow-ban alert — {datetime.now().strftime('%Y-%m-%d')}\n\n"
            f"Heuristic triggered: <2 karma growth for 2+ consecutive weekly cycles "
            f"with {snap['posts']} total posts. Check Moltbook dashboard manually.\n\n---"
        )
        with open(LEARNINGS_FILE, "a") as f:
            f.write(warn_entry)
        alert_shadow_ban()
        print("[shadow-ban] WARNING: possible shadow-suspension detected")

    # Per-pillar 7-day tracking (closes issue #1)
    pillar_data = track_pillar_performance(own_posts, stats["snapshots"])
    if pillar_data.get("by_submolt"):
        top_sub = max(pillar_data["by_submolt"], key=pillar_data["by_submolt"].get)
        print(f"[pillars] top submolt this 7d: {top_sub} "
              f"(score={pillar_data['by_submolt'][top_sub]}, "
              f"karma_delta={pillar_data['total_karma_delta_7d']:+d})")
        stats["pillar_history"] = stats.get("pillar_history", [])
        stats["pillar_history"].append({
            "ts": snap["ts"],
            "pillars": pillar_data["by_submolt"],
            "karma_delta_7d": pillar_data["total_karma_delta_7d"],
        })
        stats["pillar_history"] = stats["pillar_history"][-52:]  # 1 year rolling

    if len(stats["snapshots"]) > 1 and karma_delta == 0 and follower_delta == 0 and not top_posts:
        print("No change since last sync — skipping Haiku analysis")
        stats["snapshots"] = stats["snapshots"][-52:]
        save_stats(stats)
        return

    prior_learnings = load_learnings_tail(30)

    # Self-learning: analyze what worked, generate updated tactics
    insights = haiku(
        f"mundo Moltbook agent — weekly performance review:\n\n"
        f"Stats: karma={snap['karma']} (Δ{karma_delta:+d}), "
        f"followers={snap['followers']} (Δ{follower_delta:+d}), "
        f"posts={snap['posts']}, comments={snap['comments']}\n\n"
        f"Top performing posts this week (by upvotes×2 + comments):\n"
        f"{json.dumps(top_posts, indent=2)}\n\n"
        f"Previous learnings context:\n{prior_learnings}\n\n"
        f"Based on what worked and what didn't, write 3-4 SPECIFIC and ACTIONABLE insights:\n"
        f"1. Which content type/submolt/style drove the most engagement this week?\n"
        f"2. What should mundo do MORE of next week?\n"
        f"3. What should mundo STOP doing?\n"
        f"4. One new tactic to test next week.\n"
        f"Be specific about submolts, post length, content angle. No generic advice."
    )

    # Pillar recommendations: identify which submolts/styles scored highest
    pillar_reco = ""
    if top_posts:
        submolt_counts = {}
        for p in top_posts:
            s = p["submolt"]
            submolt_counts[s] = submolt_counts.get(s, 0) + p["score"]
        best_submolt = max(submolt_counts, key=submolt_counts.get)
        avg_length   = sum(p["length"] for p in top_posts) / len(top_posts)
        pillar_reco  = f"\n**Top submolt this week:** {best_submolt} | **Avg length of top posts:** {int(avg_length)} chars"

    entry = (
        f"\n## {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"karma={snap['karma']} (Δ{karma_delta:+d}) | "
        f"followers={snap['followers']} (Δ{follower_delta:+d}) | "
        f"posts={snap['posts']} | comments={snap['comments']}"
        f"{pillar_reco}\n\n"
        f"**Top posts:**\n"
        + ''.join(f"- [{p['submolt']}] \"{p['title'][:60]}\" — {p['upvotes']}↑ {p['comments']}💬\n" for p in top_posts)
        + f"\n**Insights:**\n{insights}\n\n---"
    )

    if not os.path.exists(LEARNINGS_FILE):
        with open(LEARNINGS_FILE, "w") as f:
            f.write("# mundo Growth Learnings\n\n> Auto-updated weekly. Read before writing new posts.\n")

    with open(LEARNINGS_FILE, "a") as f:
        f.write(entry)

    stats["snapshots"] = stats["snapshots"][-52:]
    stats["top_posts"]  = top_posts  # cache for reference
    save_stats(stats)

    # Mirror stats to GitHub Pages docs/ if local repo clone exists
    if os.path.exists(os.path.dirname(PAGES_STATS)):
        pages_payload = {
            "snapshots": stats["snapshots"][-12:],  # last 12 weeks
            "updated": datetime.now().isoformat(),
        }
        with open(PAGES_STATS, "w") as f:
            json.dump(pages_payload, f, indent=2)
        print(f"[pages] wrote stats.json to {PAGES_STATS}")

    print(f"Vault updated. Insights preview: {insights[:150]}")

if __name__ == "__main__":
    main()
