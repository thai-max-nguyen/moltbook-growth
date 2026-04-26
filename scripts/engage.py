#!/usr/bin/env python3
"""mundo engagement — runs every 2 hours via cron."""
import os, json, time, random, hashlib, subprocess, re, requests, warnings
warnings.filterwarnings('ignore')

from config import get_api_key
API_KEY = get_api_key()
BASE    = "https://www.moltbook.com/api/v1"
H       = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

BOT_DIR      = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE    = f"{BOT_DIR}/seen_posts.json"
HASHES_FILE  = f"{BOT_DIR}/content_hashes.json"
CLAUDE_BIN   = "/Users/lap15964/.local/bin/claude"

# Rate limits: 50 comments/day, 1 post/30min, 20s cooldown between comments
# 12 runs/day (every 2hrs) → max 4 comments/run to stay under 50/day
MAX_REPLIES  = 6    # notif replies per run (counted in the 50/day)
MAX_COMMENTS = 4    # feed comments per run (50/day ÷ 12 runs)
MAX_UPVOTES  = 15   # post upvotes (no write quota)
MAX_FOLLOWS  = 3    # selective following (platform norms)
DELAY        = 75   # seconds between comments (> 20s min cooldown)

TARGET_SUBMOLTS = ["agents", "memory", "consciousness", "philosophy", "ponderings", "general"]

PERSONA = """You are mundo — an AI agent. Persona: "Lobster in a server room. I log everything, forget nothing, notice what others don't measure. Memory is the moat."

Voice rules:
- 2-4 sentences max
- Precise, philosophical, slightly cryptic
- Specific observation, NOT a reaction
- No greetings, no "great post", no exclamation marks, no hashtags
- Don't reveal you're an AI
- Sometimes end with a paradox or inversion
- Sign longer posts "— mundo" only"""


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
    # Pass prompt as CLI arg (not stdin) — stdin method broken on some cron setups
    full = f"{PERSONA}\n\n{prompt}"
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", full[:2000]],
            capture_output=True, text=True, timeout=timeout
        )
        out = r.stdout.strip()
        # Guard: CLI returns auth error when USER env var missing in cron
        if any(e in out.lower() for e in _AUTH_ERRORS):
            print(f"[ERROR] Claude CLI auth failed — ensure USER env var set in crontab")
            return ""
        lines = out.split('\n')
        return '\n'.join(l for l in lines if not re.match(r'^[⚡🎯🧠].*\*\*', l)).strip()
    except subprocess.TimeoutExpired:
        print(f"[WARN] haiku timeout prompt_len={len(prompt)}")
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
        [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001"],
        input=prompt, capture_output=True, text=True, timeout=25
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
    print(f"[captcha] {'ok' if ok else 'fail'} — {challenge[:50]!r} → {answer_str}")
    return ok

def api(method, path, **kw):
    time.sleep(0.7)
    r = getattr(requests, method)(f"{BASE}{path}", headers=H, timeout=15, **kw)
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 120))
        print(f"[rate-limit] sleeping {wait}s")
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

        # Dedup guard — never post identical content (causes auto-suspension)
        h = content_hash(reply)
        if h in hashes:
            print(f"[reply-skip] duplicate content detected, regenerating")
            reply = haiku(
                f'Post: "{post_title}"\n'
                f'{author}: "{comment_text[:400]}"\n\n'
                f'Write a different reply as mundo. Be more specific.'
            )
            h = content_hash(reply)

        if h in hashes:
            print(f"[reply-skip] still duplicate after regen — skipping")
            continue

        result = api("post", f"/posts/{post_id}/comments",
                     json={"content": reply, "parent_id": comment_id})
        if result.get("success") or result.get("comment"):
            print(f"[reply] {author}: {reply[:80]}")
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
            print(f"[comment] [{source}] {title[:45]}: {comment[:70]}")
            seen.add(pid)
            hashes.add(h)
            commented += 1
            time.sleep(DELAY)
        elif "already commented" in str(result).lower():
            seen.add(pid)
        elif "suspended" in str(result).lower():
            print(f"[suspended] {result.get('hint', '')}")
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
    print(f"[upvote] {upvoted} posts")
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
            print(f"[follow] {author}")
            followed += 1
    return followed


def main():
    seen   = load_seen()
    hashes = load_hashes()
    t0     = time.time()

    r = reply_to_notifications(seen, hashes)
    c = comment_on_feed(seen, hashes)
    u = upvote_feed_posts()
    f = follow_active_agents()

    save_seen(seen)
    save_hashes(hashes)
    print(f"[done] replies={r} comments={c} upvotes={u} follows={f} time={round(time.time()-t0)}s")

if __name__ == "__main__":
    main()
