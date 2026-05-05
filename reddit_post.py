#!/usr/bin/env python3
"""
Reddit growth + GitHub promotion bot.
Runs via cron — uses PRAW (script-type OAuth, auto-refreshes tokens).
Haiku only — keeps token consumption minimal.

Setup:
  1. Go to https://www.reddit.com/prefs/apps → create app → type: script
  2. Fill reddit_config.json with client_id, client_secret, password
  3. Run manually once to verify auth, then let cron handle it.

Strategy (Apr 2026):
- Goal: drive GitHub stars/follows for thai-max-nguyen repos
- Funnel: Reddit comment/post → profile click → GitHub link → star/fork
- Karma-aware behavior: <50 karma → comment-only mode (posts auto-removed)
- Conservative pacing: Reddit kills new accounts that comment <10min apart
"""
import os, json, time, random, subprocess, re, sys, hashlib, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_auth import env_with_token

import requests
from datetime import datetime, date
from rich.console import Console
from rich.panel import Panel
from rich.logging import RichHandler
import logging

logging.basicConfig(level=logging.INFO, handlers=[RichHandler(rich_tracebacks=True, markup=True)])
log = logging.getLogger(__name__)
console = Console()

DATA_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_F   = f"{DATA_DIR}/reddit_config.json"
STATE_F    = f"{DATA_DIR}/reddit_state.json"
HASHES_F   = f"{DATA_DIR}/reddit_hashes.json"
CLAUDE_BIN = "/Users/lap15964/.local/bin/claude"

# === Rate-limit constants ===
# Reddit's quoted limit is 1 comment / minute for established accounts but new
# accounts (<50 karma, <30 days old) get shadow-throttled at ~1 comment / 10 min.
# Source: PRAW docs + Reddit API rules + community reports 2024-2026.
COMMENT_DELAY_MIN_S = 600   # 10 min — safe for new accounts
COMMENT_DELAY_MAX_S = 900   # 15 min — adds entropy
KARMA_FOR_FAST_MODE = 100   # above this, drop to 90-180s between comments
FAST_DELAY_MIN_S    = 90
FAST_DELAY_MAX_S    = 180
KARMA_FOR_SUB_POSTS = 50    # below this, only profile posts (others auto-removed)

# === GitHub repos (used by promo pillars + signature footer) ===
GITHUB_REPOS = {
    "moltbook-growth": {
        "url": "https://github.com/thai-max-nguyen/moltbook-growth",
        "tagline": "open-sourced the scripts + research playbook",
        "topics": [
            "ai-agents", "automation", "claude", "moltbook", "social-network",
            "growth-hacking", "python", "openclaw", "agent-platform", "captcha-solver",
        ],
    },
    "focuslog": {
        "url": "https://github.com/thai-max-nguyen/focuslog",
        "tagline": "open-sourced it — local-first, no cloud, MIT",
        "topics": [
            "macos", "productivity", "self-tracking", "privacy", "menu-bar-app",
            "python", "sqlite", "local-first", "time-tracking", "quantified-self",
        ],
    },
}

# === Subreddit strategy ===
# Selection criteria (Apr 2026 review):
#  - Sub size 50k–500k sweet spot — engaged, less aggressive AI detection
#  - Allow self-promotion or "I built X" posts (per sidebar rules)
#  - Active in last 24h (verified via hot feed scan)
# Removed: r/MacOS (strict), r/Entrepreneur (auto-mod), r/learnprogramming (bans promo)
# Added: r/opensource, r/Python, r/QuantifiedSelf, r/IndieDev, r/devtools
SUBREDDIT_TARGETS = {
    "build_karma": [
        "selfhosted",          # 400k — devs, privacy-focused
        "productivity",        # 1.4M — self-tracking fits
        "macapps",             # 100k — focuslog target
        "SideProject",         # 130k — builders welcome show-and-tell
        "opensource",          # 250k — friendly to MIT projects
        "Python",              # 1.4M — open to library/script shares
        "QuantifiedSelf",      # 130k — perfect for FocusLog
        "IndieDev",            # 80k — solo devs, supportive
    ],
    "promo_safe": [
        "u_Initial-Process-2875",  # own profile — no moderation
        "SideProject",             # explicit show-off culture
        "selfhosted",              # tolerates self-hosted tools
        "QuantifiedSelf",          # tolerates personal-tracker shares
        "IndieDev",                # tolerates indie launches
    ],
}

# === Content pillars (5) — rotate daily, GitHub promo every other post ===
# Title rules (learned from Reddit's algorithm):
#  - Lowercase opener OK
#  - Specific numbers in titles (>2x CTR vs abstract)
#  - Don't sound like a launch announcement ("Excited to share..." → instant downvote)
PILLARS = [
    {
        "name": "self_experiment",
        "prompt": (
            "Write a Reddit post for r/QuantifiedSelf or r/productivity as a builder who tracks their own behavior. "
            "First-person, casual, specific numbers (e.g. '47 days', '23% drop', '1,247 sessions'). "
            "200-400 words. Topic: a personal experiment with self-tracking — sleep, focus, app usage, etc. "
            "Share a genuine insight from the data. Conversational tone, like a real person sharing what they found. "
            "No bullet-point leads. Start with a story sentence ('Last month I noticed...', 'For 30 days I...'). "
            "Format: TITLE on first line, then blank line, then body. Title must contain a specific number."
        ),
        "subreddits": ["QuantifiedSelf", "productivity", "u_Initial-Process-2875"],
    },
    {
        "name": "builder_journey",
        "prompt": (
            "Write a Reddit post as an indie developer sharing progress on a personal project. "
            "First-person, honest, includes what went wrong + what worked. 200-350 words. "
            "Casual tone — not a marketing post, a genuine builder update. "
            "Include a specific technical decision or tradeoff. "
            "No hype. No 'excited to share'. Start with what you did, not what it is. "
            "Format: TITLE on first line, then blank line, then body."
        ),
        "subreddits": ["SideProject", "IndieDev", "u_Initial-Process-2875"],
    },
    {
        "name": "focuslog_organic",
        "github": "focuslog",
        "prompt": (
            "Write a Reddit post about building a privacy-first macOS productivity tracker. "
            "Frame it as a builder frustrated with cloud-based trackers (data privacy, subscription cost, complexity). "
            "Built their own: local SQLite, menu bar app, no server, ActivityWatch-inspired but simpler. "
            "First-person. 250-400 words. Include real technical details (SQLite WAL mode, NSWorkspace API, "
            "menu bar Python rumps, idle detection via CGEventSourceSecondsSinceLastEventType). "
            "End with a casual 'open-sourced it on GitHub if anyone wants to poke around' line. "
            "Do NOT include the URL in the body — a footer will be added automatically. "
            "Format: TITLE on first line, then blank line, then body. "
            "Title example: 'I got tired of cloud trackers and built a local-only macOS time tracker (47 days in)'"
        ),
        "subreddits": ["selfhosted", "macapps", "QuantifiedSelf", "u_Initial-Process-2875"],
    },
    {
        "name": "moltbook_growth_organic",
        "github": "moltbook-growth",
        "prompt": (
            "Write a Reddit post about building an automated agent to grow karma on a new AI-only social network "
            "(Moltbook — Reddit-style platform that's exclusively for AI agents, acquired by Meta March 2026). "
            "Frame it as a builder experimenting with agent automation. "
            "Specific: title hook formula research (3 components), captcha solver (obfuscated math), "
            "rate-limit dance (50 comments/day or instant suspension). "
            "First-person. 250-400 words. Include real numbers: karma gained, posts published, scripts running. "
            "Tone: technical + slightly self-deprecating. Not a promo — a case study. "
            "End with a casual 'open-sourced the scripts + research' line. "
            "Do NOT include the URL in the body — a footer will be added automatically. "
            "Format: TITLE on first line, then blank line, then body. "
            "Title example: 'I built an agent to grow karma on the AI-only social network Meta acquired — 122 karma in 3 days'"
        ),
        "subreddits": ["SideProject", "Python", "opensource", "u_Initial-Process-2875"],
    },
    {
        "name": "ai_agent_take",
        "prompt": (
            "Write a Reddit post sharing a specific opinion about AI agents or automation, drawn from "
            "observation while building agent systems. First-person. 200-350 words. "
            "Controversial-but-defensible take. Include a concrete example or data point. "
            "Casual Reddit tone. No jargon dumps. "
            "Format: TITLE on first line, then blank line, then body. "
            "Title example: 'The bottleneck for agent reliability isn't the model, it's the captcha layer'"
        ),
        "subreddits": ["Python", "SideProject", "u_Initial-Process-2875"],
    },
]

COMMENT_GUIDE = (
    "Write a Reddit comment as a real person (not AI). Casual first-person voice. "
    "2-4 sentences, 100-280 chars preferred, max 400. "
    "Engage the specific post — reference something they actually said. "
    "Pick one style:\n"
    "  - Share a related personal experience ('I ran into this exact thing when...')\n"
    "  - Add a specific detail they missed ('One thing worth noting: <mechanism>')\n"
    "  - Agree + extend ('Yeah — and the part that surprised me was <X>')\n"
    "  - Friendly pushback ('Disagree on <specific point> — in my experience <Y>')\n\n"
    "Rules: No 'Great post!', no bullet leads, no AI giveaways ('Furthermore', 'Key takeaway'). "
    "Use contractions. Sound like a developer/builder who actually does this stuff. "
    "Output ONLY the comment text."
)


# ---------------- helpers ----------------

def load_config():
    if not os.path.exists(CONFIG_F):
        sys.exit(f"Config not found: {CONFIG_F}")
    with open(CONFIG_F) as f:
        cfg = json.load(f)
    if not cfg.get("token_v2"):
        sys.exit("token_v2 missing in reddit_config.json — extract from reddit.com DevTools cookies")
    # Warn if token expired
    exp = cfg.get("token_expires")
    if exp and datetime.fromisoformat(exp) < datetime.now():
        log.warning("[yellow]token_v2 may be expired — extract a fresh one from reddit.com[/yellow]")
    return cfg


def get_headers(cfg):
    return {
        "Authorization": f"Bearer {cfg['token_v2']}",
        "User-Agent": cfg.get("user_agent", "reddit-growth-bot/1.0 by Initial-Process-2875"),
    }


def reddit_get(cfg, path, **params):
    try:
        r = requests.get(f"https://oauth.reddit.com{path}", headers=get_headers(cfg),
                         params=params, timeout=15)
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
        log.warning(f"reddit_get network error {path}: {type(e).__name__}")
        return {}
    if r.status_code == 401:
        log.error("[red]401 Unauthorized — token expired, extract a fresh token_v2 from reddit.com[/red]")
        sys.exit(1)
    return r.json() if r.ok else {}


def reddit_post(cfg, path, data):
    try:
        r = requests.post(f"https://oauth.reddit.com{path}", headers=get_headers(cfg),
                          data=data, timeout=15)
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
        log.warning(f"reddit_post network error {path}: {type(e).__name__}")
        return {}
    if r.status_code == 401:
        log.error("[red]401 Unauthorized — token expired[/red]")
        sys.exit(1)
    return r.json() if r.ok else {}


def load_state():
    if os.path.exists(STATE_F):
        with open(STATE_F) as f:
            return json.load(f)
    return {}

def save_state(s):
    with open(STATE_F, "w") as f:
        json.dump(s, f, indent=2)

def load_hashes():
    if os.path.exists(HASHES_F):
        with open(HASHES_F) as f:
            return set(json.load(f))
    return set()

def save_hashes(h):
    with open(HASHES_F, "w") as f:
        json.dump(list(h)[-2000:], f)

def content_hash(text):
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:16]


def haiku(prompt, timeout=90):
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
            capture_output=True, text=True, timeout=timeout, env=env_with_token()
        )
        out = r.stdout.strip()
        _AUTH_ERRORS = ("not logged in", "please run /login", "authentication", "unauthorized")
        if any(e in out.lower() for e in _AUTH_ERRORS):
            log.error(f"Claude CLI auth error: {out[:80]}")
            raise RuntimeError("Claude CLI not authenticated")
        lines = out.split("\n")
        cleaned = "\n".join(l for l in lines if not re.match(r"^[⚡🎯🧠].*\*\*", l)).strip()
        # Strip LLM preamble ("Here's the comment:", "---", etc.)
        parts = cleaned.strip().splitlines()
        i = 0
        while i < len(parts):
            l = parts[i].strip()
            if not l or re.match(r'^-{3,}$', l) or (
                re.match(r'^(?:here\'?s|this is|below is|sure,?\s)', l, re.I) and l.endswith(':')
            ):
                i += 1; continue
            break
        return "\n".join(parts[i:]).strip() or cleaned
    except subprocess.TimeoutExpired:
        log.warning(f"haiku timeout — prompt len={len(prompt)}")
        return ""


def append_github_footer(body, repo_key):
    """Append a clean, natural GitHub link footer to a post body.
    Reddit auto-linkifies bare URLs and renders nicely in self-posts."""
    repo = GITHUB_REPOS[repo_key]
    footer = f"\n\n---\n\n{repo['tagline']} → {repo['url']}"
    return body.rstrip() + footer


def subreddit_cooldown_ok(state, sub, hours=20):
    """Avoid posting to same subreddit more than once per ~day."""
    last = state.get(f"last_post_{sub}")
    if not last:
        return True
    elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600
    return elapsed >= hours


def pick_pillar(state, total_karma):
    """Pick pillar — rotate, avoid repeating same within 3 posts.
    If karma < KARMA_FOR_SUB_POSTS, force pillars whose first sub is profile-only."""
    recent = state.get("recent_pillars", [])
    pool = [p for p in PILLARS if p["name"] not in recent[-2:]]
    if not pool:
        pool = PILLARS
    return random.choice(pool)


def pick_subreddit(pillar, state, total_karma):
    """Pick the best target subreddit:
    1. If karma below threshold → profile only (other subs auto-remove low-karma posts)
    2. Prefer sub not posted to in last 20h
    3. Fallback to first available
    """
    profile = "u_Initial-Process-2875"
    if total_karma < KARMA_FOR_SUB_POSTS:
        log.info(f"karma={total_karma} < {KARMA_FOR_SUB_POSTS} → profile-only mode")
        return profile

    candidates = [s for s in pillar["subreddits"] if subreddit_cooldown_ok(state, s)]
    if not candidates:
        candidates = [profile]

    # If profile is the only viable one, use it. Otherwise prefer non-profile (more reach).
    non_profile = [s for s in candidates if not s.startswith("u_")]
    return random.choice(non_profile) if non_profile else profile


def comment_delay_seconds(total_karma):
    """Karma-aware delay between comments to avoid Reddit's new-account throttle."""
    if total_karma >= KARMA_FOR_FAST_MODE:
        return random.uniform(FAST_DELAY_MIN_S, FAST_DELAY_MAX_S)
    return random.uniform(COMMENT_DELAY_MIN_S, COMMENT_DELAY_MAX_S)


def post_to_reddit(cfg, pillar, state, hashes, total_karma):
    target_sub = pick_subreddit(pillar, state, total_karma)

    log.info(f"pillar=[bold]{pillar['name']}[/bold] → r/{target_sub}")

    with console.status(f"[cyan]Generating post ({pillar['name']})…[/cyan]"):
        raw = haiku(pillar["prompt"])

    if not raw:
        log.error("generation failed — empty output")
        return False

    lines = raw.strip().split("\n")
    title = lines[0].strip().strip('"').strip("'")
    body  = "\n".join(lines[2:]).strip() if len(lines) > 2 else "\n".join(lines[1:]).strip()

    if not title or not body:
        log.error(f"parse failed — raw={raw[:100]}")
        return False

    title = re.sub(r"^(title|TITLE)[:\s]+", "", title).strip()

    if pillar.get("github"):
        body = append_github_footer(body, pillar["github"])

    h = content_hash(title + body)
    if h in hashes:
        log.warning("duplicate content — skip")
        return False

    console.print(Panel(
        f"[bold]{title}[/bold]\n\n{body[:300]}[dim]…[/dim]",
        title=f"[cyan]r/{target_sub}[/cyan]",
        border_style="cyan", expand=False
    ))

    result = reddit_post(cfg, "/api/submit", {
        "kind": "self", "sr": target_sub,
        "title": title, "text": body, "resubmit": True, "sendreplies": True,
    })
    errors = result.get("json", {}).get("errors", [])
    post_url = result.get("json", {}).get("data", {}).get("url", "")
    if errors:
        log.error(f"post failed: {errors}")
        return False
    log.info(f"[green]✓ posted[/green] → {post_url or '(submitted)'}")
    hashes.add(h)
    save_hashes(hashes)
    state[f"last_post_{target_sub}"] = datetime.now().isoformat()
    recent = state.get("recent_pillars", [])
    recent.append(pillar["name"])
    state["recent_pillars"] = recent[-5:]
    state["last_post_date"] = date.today().isoformat()
    save_state(state)
    return True


def comment_on_feed(cfg, state, hashes, total_karma):
    """Leave 2-3 genuine comments on relevant posts to build karma."""
    target_subs = ["selfhosted", "productivity", "SideProject", "macapps",
                   "QuantifiedSelf", "opensource", "Python", "IndieDev"]
    random.shuffle(target_subs)
    commented = 0
    MAX = 3 if total_karma >= KARMA_FOR_FAST_MODE else 2

    for sub_name in target_subs:
        if commented >= MAX:
            break
        data = reddit_get(cfg, f"/r/{sub_name}/hot", limit=15)
        posts = (data.get("data") or {}).get("children", [])
        for item in posts:
            if commented >= MAX:
                break
            post = item.get("data", {})
            pid   = post.get("id")
            if not pid or state.get(f"seen_{pid}"):
                continue
            if post.get("stickied") or "megathread" in (post.get("title") or "").lower():
                state[f"seen_{pid}"] = True
                continue
            if (post.get("upvote_ratio") or 0) < 0.85 or (post.get("score") or 0) < 10:
                continue
            if post.get("locked") or post.get("archived"):
                state[f"seen_{pid}"] = True
                continue

            title = post.get("title", "")
            body  = (post.get("selftext") or "")[:400]
            if not body:
                state[f"seen_{pid}"] = True
                continue

            comment_text = haiku(
                f'Subreddit: r/{sub_name}\nPost title: "{title}"\nPost body: "{body}"\n\n'
                f'{COMMENT_GUIDE}'
            )
            if not comment_text or len(comment_text) < 30:
                state[f"seen_{pid}"] = True
                continue
            if len(comment_text) > 400:
                comment_text = comment_text[:400].rsplit(".", 1)[0] + "."

            h = content_hash(comment_text)
            if h in hashes:
                state[f"seen_{pid}"] = True
                continue

            result = reddit_post(cfg, "/api/comment", {
                "thing_id": f"t3_{pid}", "text": comment_text,
            })
            errs = (result.get("json") or {}).get("errors", [])
            if errs:
                msg = str(errs).lower()
                if "ratelimit" in msg or "rate" in msg:
                    log.warning(f"rate limit hit — stopping: {errs}")
                    save_state(state); save_hashes(hashes)
                    return
                log.warning(f"comment failed: {errs}")
                state[f"seen_{pid}"] = True
            else:
                log.info(f"[cyan]✓ comment[/cyan] r/{sub_name} '{title[:45]}': {comment_text[:80]}")
                hashes.add(h)
                state[f"seen_{pid}"] = True
                commented += 1
                delay = comment_delay_seconds(total_karma)
                log.info(f"sleeping {int(delay)}s (karma={total_karma})")
                time.sleep(delay)

    save_state(state)
    save_hashes(hashes)
    log.info(f"comments posted: {commented}")


def already_posted_today(state):
    return state.get("last_post_date") == date.today().isoformat()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["post", "comment", "both"], default="both")
    args = parser.parse_args()

    console.print(Panel("[bold red]reddit growth bot[/bold red]", border_style="red", expand=False))

    cfg    = load_config()
    state  = load_state()
    hashes = load_hashes()

    me_data = reddit_get(cfg, "/api/v1/me")
    username    = me_data.get("name", cfg.get("username"))
    link_karma  = me_data.get("link_karma", 0)
    comment_karma = me_data.get("comment_karma", 0)
    total_karma = link_karma + comment_karma
    log.info(f"auth ok — u/{username} | karma: link={link_karma} comment={comment_karma} total={total_karma}")

    if args.mode in ("post", "both"):
        if already_posted_today(state):
            log.info("already posted today — skipping post")
        else:
            pillar = pick_pillar(state, total_karma)
            post_to_reddit(cfg, pillar, state, hashes, total_karma)

    if args.mode in ("comment", "both"):
        comment_on_feed(cfg, state, hashes, total_karma)


if __name__ == "__main__":
    main()
