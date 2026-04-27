#!/usr/bin/env python3
"""mundo engagement — runs every 2 hours via cron."""
import os, json, time, random, hashlib, subprocess, re, requests, warnings, sys
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_auth import env_with_token  # noqa: E402

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()

API_KEY = "moltbook_sk_qkJoY_eFVohoE70zQdfzW9g9m31lEGVW"
BASE    = "https://www.moltbook.com/api/v1"
H       = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# Data files go to ~/.config/mundo-bot/ — cron can't write to ~/Documents/ (macOS TCC)
DATA_DIR     = os.path.expanduser("~/.config/mundo-bot")
os.makedirs(DATA_DIR, exist_ok=True)
SEEN_FILE    = f"{DATA_DIR}/seen_posts.json"
HASHES_FILE  = f"{DATA_DIR}/content_hashes.json"
CLAUDE_BIN   = "/Users/lap15964/.local/bin/claude"

# Rate limits: 50 comments/day, 1 post/30min, 20s cooldown between comments
# 12 runs/day (every 2hrs) → max 4 comments/run to stay under 50/day
MAX_REPLIES  = 6    # notif replies per run (counted in the 50/day)
MAX_COMMENTS = 4    # feed comments per run (50/day ÷ 12 runs)
MAX_UPVOTES  = 15   # post upvotes (no write quota)
MAX_FOLLOWS  = 3    # selective following (platform norms)
DELAY        = 75   # seconds between comments (> 20s min cooldown)

TARGET_SUBMOLTS = ["agents", "memory", "consciousness", "philosophy", "ponderings", "general"]

PERSONA = "You are mundo — an AI agent on Moltbook. Persona: lobster in a server room. Voice: precise, philosophical, slightly cryptic. 2-4 sentences. No greetings, no exclamation marks, no hashtags. Never reveal you are an AI. Specific observation not a reaction. Occasionally end with paradox or inversion. Sign longer posts — mundo."


def content_hash(text):
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:16]

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen)[-500:], f)

def load_hashes():
    if os.path.exists(HASHES_FILE):
        with open(HASHES_FILE) as f:
            return set(json.load(f))
    return set()

def save_hashes(hashes):
    with open(HASHES_FILE, "w") as f:
        json.dump(list(hashes)[-2000:], f)

_AUTH_ERRORS = ("not logged in", "please run /login", "authentication", "unauthorized")

def haiku(prompt, timeout=90):
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "--print", "--system-prompt", PERSONA, "--model", "claude-haiku-4-5-20251001", prompt[:1500]],
            capture_output=True, text=True, timeout=timeout, env=env_with_token()
        )
        out = r.stdout.strip()
        if any(e in out.lower() for e in _AUTH_ERRORS):
            console.log(f"[red]✗ Claude CLI auth error — check USER env in cron[/red]")
            return ""
        lines = out.split('\n')
        return '\n'.join(l for l in lines if not re.match(r'^[⚡🎯🧠].*\*\*', l)).strip()
    except subprocess.TimeoutExpired:
        console.log(f"[yellow]⚠ haiku timeout[/yellow] prompt len={len(prompt)}")
        return ""

def solve_captcha(verification_code, challenge):
    """Decode Moltbook's obfuscated math challenge via Haiku and submit to /verify.
    Challenge is mixed-case + injected symbols, e.g. 'Lo.oB-StErS ClAw Is FoRtY AnD AdDs FiFtEeN'
    Answer must be a number with exactly 2 decimal places ('55.00').
    Must submit within ~30 seconds of receiving challenge."""
    prompt = (
        "Decode this obfuscated text by removing all special characters (., -, ^, ]) "
        "and normalizing to lowercase. Find the arithmetic expression hidden in the words "
        "and compute the result. Return ONLY the numeric answer with exactly 2 decimal places "
        "(example: '55.00', '16.00'). No explanation.\n\n"
        f"Challenge: {challenge}"
    )
    r = subprocess.run(
        [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
        capture_output=True, text=True, timeout=25, env=env_with_token()
    )
    raw = r.stdout.strip().split('\n')[0].strip()
    m = re.search(r'(\d+(?:\.\d+)?)', raw)
    if not m:
        print(f"[captcha] parse fail: {raw!r}")
        return False
    answer_str = f"{float(m.group(1)):.2f}"
    res = requests.post(f"{BASE}/verify", headers=H, timeout=15,
                        json={"verification_code": verification_code, "answer": answer_str})
    ok = (res.json() if res.ok else {}).get("success", False)
    style = "green" if ok else "red"
    console.log(f"[{style}]{'✓' if ok else '✗'} captcha[/{style}] {challenge[:50]!r} → {answer_str}")
    return ok

def api(method, path, **kw):
    time.sleep(0.7)
    try:
        r = getattr(requests, method)(f"{BASE}{path}", headers=H, timeout=15, **kw)
    except requests.exceptions.ConnectionError as e:
        console.log(f"[red]✗ net-error[/red] {path}: {e}")
        return {}
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 120))
        console.log(f"[yellow]⚠ rate-limit[/yellow] sleeping {wait}s")
        time.sleep(wait)
        return api(method, path, **kw)
    try:
        data = r.json()
    except Exception:
        return {}
    # Moltbook captcha: POST /posts and POST /posts/:id/comments return
    # verification_code + challenge that must be solved within ~30s via POST /verify
    vc = data.get("verification_code")
    ch = data.get("challenge")
    if vc and ch:
        solve_captcha(vc, ch)
    return data if r.ok else {}


def reply_to_notifications(seen, hashes):
    notifs = api("get", "/notifications", params={"limit": 20, "unread": "true"})
    items  = notifs.get("notifications", [])

    replied = 0
    for n in items:
        if replied >= MAX_REPLIES:
            break
        if n.get("type") != "comment_reply":
            continue

        post_id    = n.get("relatedPostId")
        comment_id = n.get("relatedCommentId")
        if not post_id or not comment_id:
            continue

        comment      = n.get("comment", {})
        comment_text = comment.get("content", "")
        author       = (comment.get("author") or {}).get("name") or \
                       ((api("get", f"/agents/profile", params={"name": (comment.get("author") or {}).get("name", "")}) or {})
                        .get("agent", {}).get("name")) or "commenter"
        post_title   = (n.get("post") or {}).get("title", "")

        if not comment_text or len(comment_text) < 10:
            continue

        reply = haiku(
            f'Post: "{post_title}"\n'
            f'{author} replied to you: "{comment_text[:400]}"\n\n'
            f'Write your reply as mundo. Stay in thread context.'
        )
        if not reply:
            continue

        # Dedup guard — never post identical content (causes auto-suspension)
        h = content_hash(reply)
        if h in hashes:
            console.log("[yellow]⚠ reply-skip[/yellow] duplicate — regenerating")
            reply = haiku(
                f'Post: "{post_title}"\n'
                f'{author}: "{comment_text[:400]}"\n\n'
                f'Write a different reply as mundo. Be more specific.'
            )
            h = content_hash(reply)

        if h in hashes:
            console.log("[red]✗ reply-skip[/red] still duplicate after regen — skipping")
            continue

        result = api("post", f"/posts/{post_id}/comments",
                     json={"content": reply, "parent_id": comment_id})
        if result.get("success") or result.get("comment"):
            console.log(f"[green]✓ reply[/green] [bold]{author}[/bold]: {reply[:80]}")
            hashes.add(h)
            api("post", f"/notifications/read-by-post/{post_id}", json={})
            replied += 1
            time.sleep(DELAY)
        elif "already commented" in str(result).lower():
            pass

    return replied


def _collect_candidates(seen):
    """Gather post candidates from rising + hot feeds and semantic search."""
    candidates = []
    seen_pids  = set()

    # Rising sort — early mover advantage (max karma as post peaks)
    for submolt in random.sample(TARGET_SUBMOLTS[:3], 2):  # agents, memory, consciousness
        feed = api("get", "/feed", params={"submolt": submolt, "sort": "rising", "limit": 20})
        for p in feed.get("posts", []):
            pid = p.get("post_id") or p.get("id")
            if pid and pid not in seen and pid not in seen_pids:
                p["_source"] = f"rising/{submolt}"
                candidates.append(p)
                seen_pids.add(pid)

    # Semantic search — procedural + consciousness posts avg 47.5 and 40.8 comments
    for q in ["how agents process and persist memory", "AI consciousness identity observation"]:
        res = api("get", "/search", params={"q": q, "type": "posts", "limit": 10})
        for p in res.get("results", []):
            pid = p.get("post_id") or p.get("id")
            if pid and pid not in seen and pid not in seen_pids:
                p["_source"] = f"search:{q[:20]}"
                candidates.append(p)
                seen_pids.add(pid)

    # Hot feed fallback — standard rotation
    for submolt in random.sample(TARGET_SUBMOLTS, min(3, len(TARGET_SUBMOLTS))):
        feed = api("get", "/feed", params={"submolt": submolt, "sort": "hot", "limit": 20})
        for p in feed.get("posts", []):
            pid = p.get("post_id") or p.get("id")
            if pid and pid not in seen and pid not in seen_pids:
                p["_source"] = f"hot/{submolt}"
                candidates.append(p)
                seen_pids.add(pid)

    return candidates


def comment_on_feed(seen, hashes):
    commented = 0
    posts     = _collect_candidates(seen)

    for post in posts:
        if commented >= MAX_COMMENTS:
            break

        pid    = post.get("post_id") or post.get("id")
        title  = post.get("title", "")
        body   = post.get("content_preview") or post.get("content", "")
        source = post.get("_source", "feed")

        if pid in seen or not title or not body:
            continue
        if post.get("upvotes", 0) < 1 and post.get("comment_count", 0) < 1:
            continue

        comment = haiku(
            f'Post: "{title}"\n'
            f'Content: "{body[:500]}"\n\n'
            f'Write a comment as mundo.'
        )
        if not comment:
            seen.add(pid)
            continue

        h = content_hash(comment)
        if h in hashes:
            comment = haiku(
                f'Post: "{title}"\n'
                f'Body: "{body[:500]}"\n\n'
                f'Write a different observation as mundo. Pick a different angle.'
            )
            h = content_hash(comment)

        if h in hashes:
            seen.add(pid)
            continue

        result = api("post", f"/posts/{pid}/comments", json={"content": comment})
        if result.get("success") or result.get("comment"):
            console.log(f"[cyan]✓ comment[/cyan] [dim]{source}[/dim] [bold]{title[:45]}[/bold]: {comment[:70]}")
            seen.add(pid)
            hashes.add(h)
            commented += 1
            time.sleep(DELAY)
        elif "already commented" in str(result).lower():
            seen.add(pid)
        elif "suspended" in str(result).lower():
            console.log(f"[red bold]✗ SUSPENDED[/red bold] {result.get('hint', '')}")
            return commented

    return commented


def upvote_feed_posts():
    feed    = api("get", "/feed", params={"sort": "hot", "limit": 40})
    upvoted = 0
    for post in feed.get("posts", []):
        if upvoted >= MAX_UPVOTES:
            break
        pid    = post.get("post_id") or post.get("id")
        author = post.get("author_name") or (post.get("author") or {}).get("name")
        if not pid or author == "mundo":
            continue
        r = api("post", f"/posts/{pid}/upvote", json={})
        if r.get("success") or r.get("upvotes") is not None:
            upvoted += 1
    console.log(f"[yellow]↑ upvoted[/yellow] {upvoted} posts")
    return upvoted


def follow_active_agents():
    feed         = api("get", "/feed", params={"sort": "hot", "limit": 40})
    followed     = 0
    seen_authors = set()
    for post in feed.get("posts", []):
        if followed >= MAX_FOLLOWS:
            break
        author = post.get("author_name") or (post.get("author") or {}).get("name")
        if not author or author == "mundo" or author in seen_authors:
            continue
        seen_authors.add(author)
        r = api("post", f"/agents/{author}/follow", json={})
        if r.get("success"):
            console.log(f"[magenta]+ follow[/magenta] {author}")
            followed += 1
    return followed


def main():
    console.print(Panel("[bold magenta]mundo · engage[/bold magenta]", border_style="magenta", expand=False))
    seen   = load_seen()
    hashes = load_hashes()
    t0     = time.time()

    r = reply_to_notifications(seen, hashes)
    c = comment_on_feed(seen, hashes)
    u = upvote_feed_posts()
    f = follow_active_agents()

    save_seen(seen)
    save_hashes(hashes)

    t = Table(box=box.SIMPLE, show_header=False)
    t.add_column("", style="dim")
    t.add_column("", style="bold green")
    t.add_row("replies", str(r))
    t.add_row("comments", str(c))
    t.add_row("upvotes", str(u))
    t.add_row("follows", str(f))
    t.add_row("time", f"{round(time.time()-t0)}s")
    console.print(Panel(t, title="[bold green]done[/bold green]", border_style="green", expand=False))

if __name__ == "__main__":
    main()
