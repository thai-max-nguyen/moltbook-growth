#!/usr/bin/env python3
"""mundo vault sync — runs weekly (Sunday 1:00 UTC).
Analyzes own post performance, generates actionable insights, saves to vault learnings."""
import os, json, time, subprocess, re, requests, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

from config import get_api_key
API_KEY = get_api_key()
BASE     = "https://www.moltbook.com/api/v1"
H        = {"Authorization": f"Bearer {API_KEY}"}

BOT_DIR        = os.path.dirname(os.path.abspath(__file__))
STATS_FILE     = f"{BOT_DIR}/mundo_stats.json"
LEARNINGS_FILE = f"{BOT_DIR}/mundo_learnings.md"
CLAUDE_BIN     = "/Users/lap15964/.local/bin/claude"

def api_get(path, **kw):
    time.sleep(0.5)
    r = requests.get(f"{BASE}{path}", headers=H, timeout=15, **kw)
    return r.json() if r.ok else {}

def haiku(prompt):
    r = subprocess.run(
        [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001"],
        input=prompt, capture_output=True, text=True, timeout=90
    )
    lines = r.stdout.strip().split('\n')
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

def main():
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

    if len(stats["snapshots"]) > 0 and karma_delta == 0 and follower_delta == 0 and not top_posts:
        print("No change since last sync — skipping Haiku analysis")
        stats["snapshots"].append(snap)
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

    stats["snapshots"].append(snap)
    stats["snapshots"] = stats["snapshots"][-52:]
    stats["top_posts"]  = top_posts  # cache for reference
    save_stats(stats)
    print(f"Vault updated. Insights preview: {insights[:150]}")

if __name__ == "__main__":
    main()
