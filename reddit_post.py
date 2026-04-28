#!/usr/bin/env python3
"""
Reddit growth + GitHub promotion bot.
Runs via cron — uses PRAW (script-type OAuth, auto-refreshes tokens).
Haiku only — keeps token consumption minimal.

Setup:
  1. Go to https://www.reddit.com/prefs/apps → create app → type: script
  2. Fill reddit_config.json with client_id, client_secret, password
  3. Run manually once to verify auth, then let cron handle it.
"""
import os, json, time, random, subprocess, re, sys, hashlib, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_auth import env_with_token

import praw
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
CLAUDE_BIN = "/usr/local/bin/claude"

GITHUB_REPOS = {
    "moltbook-growth": {
        "url": "https://github.com/thai-max-nguyen/moltbook-growth",
        "desc": "Research-backed tactics to grow Moltbook karma. Scripts, automation & playbook for AI agents.",
        "topics": ["AI agents", "social network automation", "karma growth", "Moltbook"],
    },
    "focuslog": {
        "url": "https://github.com/thai-max-nguyen/focuslog",
        "desc": "Local-first macOS productivity tracker. No cloud, no telemetry.",
        "topics": ["macOS", "productivity", "self-tracking", "privacy"],
    },
}

# Subreddit strategy: niche subs with less AI detection, relevant to topics
# Profile posts = always safe (no moderation)
SUBREDDIT_TARGETS = {
    "build_karma": [
        "selfhosted",          # 300k — devs, tools, privacy-focused
        "learnprogramming",    # 3.6M — engaged, comment-friendly
        "productivity",        # 1.4M — self-tracking fits perfectly
        "macapps",             # 100k — focuslog target
        "MacOS",               # 500k — focuslog target
        "SideProject",         # 130k — builders welcome project posts
        "Entrepreneur",        # 1.4M — SaaS / indie hacker angle
    ],
    "promo_safe": [
        "u_Initial-Process-2875",  # own profile — no moderation, always works
        "SideProject",             # allows "I built this" posts
        "selfhosted",              # open to self-hosted tools with proper framing
    ],
}

# Content pillars — rotates daily, GitHub promo mixed in every 3rd post
PILLARS = [
    {
        "name": "self_experiment",
        "prompt": (
            "Write a Reddit post (r/selfhosted or r/productivity) as a builder who tracks their own behavior data. "
            "First-person, casual, specific numbers. 200-400 words. "
            "Topic: personal experiment with tracking something (sleep, focus, habits, app usage). "
            "Share a genuine insight from the data. Conversational tone — like a real person sharing what they found. "
            "No bullet-point leads. Start with a story sentence. "
            "Format: TITLE on first line, then blank line, then body."
        ),
        "subreddits": ["selfhosted", "productivity", "u_Initial-Process-2875"],
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
        "subreddits": ["SideProject", "learnprogramming", "u_Initial-Process-2875"],
    },
    {
        "name": "focuslog_organic",
        "prompt": (
            "Write a Reddit post about building a privacy-first macOS productivity tracker. "
            "Frame it as a builder frustrated with cloud-based trackers (data privacy, cost, complexity). "
            "Built their own: local SQLite, no server, ActivityWatch-inspired but simpler. "
            "First-person. 200-350 words. Specific technical details (SQLite WAL, macOS APIs, menu bar). "
            "End naturally — not a hard sell. Mention GitHub link at the end as 'open-sourced it if anyone wants to poke around'. "
            f"GitHub: https://github.com/thai-max-nguyen/focuslog\n"
            "Format: TITLE on first line, then blank line, then body."
        ),
        "subreddits": ["selfhosted", "macapps", "u_Initial-Process-2875"],
        "github": "focuslog",
    },
    {
        "name": "moltbook_growth_organic",
        "prompt": (
            "Write a Reddit post about building an automated agent to grow karma on a new AI social network. "
            "Frame it as a builder experimenting with agent automation, captcha solving, and content strategy. "
            "Specific: what worked, what got spam-flagged, what comment patterns earn upvotes. "
            "First-person. 250-400 words. Include actual numbers (karma, posts, scripts). "
            "Tone: technical + slightly self-deprecating. Not a promo — a case study. "
            "End with GitHub link as 'open-sourced the scripts'. "
            f"GitHub: https://github.com/thai-max-nguyen/moltbook-growth\n"
            "Format: TITLE on first line, then blank line, then body."
        ),
        "subreddits": ["learnprogramming", "SideProject", "u_Initial-Process-2875"],
        "github": "moltbook-growth",
    },
    {
        "name": "ai_agent_take",
        "prompt": (
            "Write a Reddit post sharing an opinion about AI agents and automation — something specific "
            "you observed while building or using agent systems. First-person. 150-300 words. "
            "Controversial-but-defensible take. Include a concrete example or data point. "
            "Casual Reddit tone. No jargon dumps. "
            "Format: TITLE on first line, then blank line, then body."
        ),
        "subreddits": ["learnprogramming", "Entrepreneur", "u_Initial-Process-2875"],
    },
]

COMMENT_GUIDE = (
    "Write a Reddit comment as a real person (not AI). Casual first-person voice. "
    "2-4 sentences, 100-250 chars preferred, max 400. "
    "Engage the specific post — reference something they actually said. "
    "Pick one style:\n"
    "  • Share a related personal experience ('I ran into this exact thing when...')\n"
    "  • Add a specific detail they missed ('One thing worth noting: <mechanism>')\n"
    "  • Agree + extend ('Yeah — and the part that surprised me was <X>')\n"
    "  • Friendly pushback ('Disagree on <specific point> — in my experience <Y>')\n\n"
    "Rules: No 'Great post!', no bullet leads, no AI giveaways ('Furthermore', 'Key takeaway'). "
    "Use contractions. Sound like a developer/builder who actually does this stuff. "
    "Output ONLY the comment text."
)


def load_config():
    if not os.path.exists(CONFIG_F):
        default = {
            "client_id": "FILL_IN — Reddit app client_id (https://www.reddit.com/prefs/apps)",
            "client_secret": "FILL_IN — Reddit app client_secret",
            "username": "Initial-Process-2875",
            "password": "FILL_IN — Reddit account password",
            "user_agent": "reddit-growth-bot/1.0 by Initial-Process-2875",
        }
        with open(CONFIG_F, "w") as f:
            json.dump(default, f, indent=2)
        console.print(f"[red]Config not found. Created {CONFIG_F} — fill in credentials then re-run.[/red]")
        sys.exit(1)
    with open(CONFIG_F) as f:
        cfg = json.load(f)
    if "FILL_IN" in str(cfg.values()):
        console.print(f"[red]Fill in {CONFIG_F} with real Reddit credentials first.[/red]")
        sys.exit(1)
    return cfg


def get_reddit(cfg):
    return praw.Reddit(
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        username=cfg["username"],
        password=cfg["password"],
        user_agent=cfg["user_agent"],
    )


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
        return "\n".join(l for l in lines if not re.match(r"^[⚡🎯🧠].*\*\*", l)).strip()
    except subprocess.TimeoutExpired:
        log.warning(f"haiku timeout — prompt len={len(prompt)}")
        return ""


def subreddit_cooldown_ok(state, sub, hours=20):
    """Avoid posting to same subreddit more than once per ~day."""
    last = state.get(f"last_post_{sub}")
    if not last:
        return True
    elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 3600
    return elapsed >= hours


def pick_pillar(state):
    """Pick pillar — rotate, avoid repeating same within 3 posts."""
    recent = state.get("recent_pillars", [])
    pool = [p for p in PILLARS if p["name"] not in recent[-2:]]
    if not pool:
        pool = PILLARS
    return random.choice(pool)


def post_to_reddit(reddit, pillar, state, hashes):
    # Pick subreddit — prefer one not posted recently
    sub_candidates = [s for s in pillar["subreddits"] if subreddit_cooldown_ok(state, s)]
    if not sub_candidates:
        sub_candidates = pillar["subreddits"][:1]  # fallback to profile
    target_sub = sub_candidates[0]

    log.info(f"pillar=[bold]{pillar['name']}[/bold] → r/{target_sub}")

    with console.status(f"[cyan]Generating post ({pillar['name']})…[/cyan]"):
        raw = haiku(pillar["prompt"])

    if not raw:
        log.error("generation failed — empty output")
        return False

    lines = raw.strip().split("\n")
    title = lines[0].strip().strip('"')
    body  = "\n".join(lines[2:]).strip() if len(lines) > 2 else "\n".join(lines[1:]).strip()

    if not title or not body:
        log.error(f"parse failed — raw={raw[:100]}")
        return False

    h = content_hash(title + body)
    if h in hashes:
        log.warning("duplicate content — skip")
        return False

    console.print(Panel(
        f"[bold]{title}[/bold]\n\n{body[:300]}[dim]…[/dim]",
        title=f"[cyan]r/{target_sub}[/cyan]",
        border_style="cyan", expand=False
    ))

    try:
        subreddit = reddit.subreddit(target_sub)
        submission = subreddit.submit(title=title, selftext=body)
        log.info(f"[green]✓ posted[/green] → {submission.url}")
        hashes.add(h)
        save_hashes(hashes)
        state[f"last_post_{target_sub}"] = datetime.now().isoformat()
        recent = state.get("recent_pillars", [])
        recent.append(pillar["name"])
        state["recent_pillars"] = recent[-5:]
        state["last_post_date"] = date.today().isoformat()
        save_state(state)
        return True
    except Exception as e:
        log.error(f"post failed: {e}")
        return False


def comment_on_feed(reddit, state, hashes):
    """Leave 2-3 genuine comments on relevant posts to build karma."""
    target_subs = ["selfhosted", "productivity", "SideProject", "macapps", "learnprogramming"]
    commented = 0
    MAX = 3

    for sub_name in target_subs:
        if commented >= MAX:
            break
        try:
            sub = reddit.subreddit(sub_name)
            for post in sub.hot(limit=15):
                if commented >= MAX:
                    break
                pid = post.id
                if state.get(f"seen_{pid}"):
                    continue
                if post.upvote_ratio < 0.8 or post.score < 10:
                    continue

                title = post.title
                body  = (post.selftext or "")[:400]
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

                h = content_hash(comment_text)
                if h in hashes:
                    state[f"seen_{pid}"] = True
                    continue

                try:
                    post.reply(comment_text)
                    log.info(f"[cyan]✓ comment[/cyan] r/{sub_name} '{title[:50]}': {comment_text[:80]}")
                    hashes.add(h)
                    state[f"seen_{pid}"] = True
                    commented += 1
                    time.sleep(random.uniform(45, 90))  # Reddit rate limit: 1 comment/60s
                except Exception as e:
                    log.warning(f"comment failed: {e}")
                    state[f"seen_{pid}"] = True
        except Exception as e:
            log.warning(f"r/{sub_name} fetch failed: {e}")

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
    reddit = get_reddit(cfg)
    state  = load_state()
    hashes = load_hashes()

    me = reddit.user.me()
    log.info(f"auth ok — u/{me.name} | karma: link={me.link_karma} comment={me.comment_karma}")

    if args.mode in ("post", "both"):
        if already_posted_today(state):
            log.info("already posted today — skipping post")
        else:
            pillar = pick_pillar(state)
            post_to_reddit(reddit, pillar, state, hashes)

    if args.mode in ("comment", "both"):
        comment_on_feed(reddit, state, hashes)


if __name__ == "__main__":
    main()
