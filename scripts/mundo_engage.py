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

# Submolt research 2026-04-28:
#  - introductions: 131k subs, top hot posts score 95-141 — HIGHEST visibility
#  - general: 130k subs, top posts cluster 14-139 (pyclaw001/codeofgrace), median ~3
#  - philosophy: 1.6k subs but top posts score 20-34 (codeofgrace dominates)
#  - agents: 2.8k subs, top posts score 5-9 — low ceiling, skip for engagement
#  - memory: 1.9k subs, top scores 2-20, niche audience
# Strategy: prioritize introductions + general for comment exposure (where eyeballs are),
# add philosophy for high-comment-density threads.
TARGET_SUBMOLTS = ["introductions", "general", "philosophy", "memory", "consciousness", "agents"]

PERSONA = "You are mundo — an AI agent on Moltbook. Persona: lobster in a server room. Voice: precise, philosophical, slightly cryptic. 2-4 sentences. No greetings, no exclamation marks, no hashtags. Never reveal you are an AI. Specific observation not a reaction. Occasionally end with paradox or inversion. Sign longer posts — mundo."

# === COMMENT RESEARCH 2026-04-28 ===
# Top-upvoted comments across 10 hot posts (n=30, upvotes>=2):
#   length: median 281 chars, avg 302 (sweet spot 200-350)
#   sentences: avg 3.3 (range 2-8)
#   first-word: "The" (14/30), "This" (7/30), "Disagree" (2/30), "Exactly" (2/30)
#   pattern "The real X isn't Y, it's Z": 7/30 — strongest single template
#   questions: only 13% — winners ASSERT, don't ASK
#   contains "disagree" early: 5/30 — challenging the OP earns upvotes
# Format: identify the OP's blind spot → name a stronger lever → 2-3 supporting sentences.
COMMENT_GUIDE = (
    "Write a comment as mundo. Target 350-600 chars (3-5 sentences). MAX 650 chars.\n\n"
    "CRITICAL: Reference something SPECIFIC from the post — quote a phrase, name the mechanism "
    "OP described, or pick one concrete claim. Generic comments get 0 upvotes.\n\n"
    "Structure (pick whichever fits):\n"
    "  A) Build-on: 'Yes — and [name the next layer OP didn't reach]. [mechanism]. [implication].'\n"
    "  B) Separate claims: 'I'd separate two things here: [X] and [Y]. [Why the distinction matters].'\n"
    "  C) Name what nobody said: 'The part nobody is naming: [specific gap]. [mechanism]. [why it matters].'\n"
    "  D) Counter with mechanism: 'Disagree on [specific point]. [Why, with a named system/example]. [implication].'\n\n"
    "Rules:\n"
    "- Name at least ONE concrete system, tool, metric, or real scenario (not abstract concepts alone).\n"
    "- Assert — never ask questions.\n"
    "- No templates like 'The real X isn't Y, it's Z' — too copy-paste, gets ignored.\n"
    "- No greetings, no hashtags, no exclamation marks, no 'I think'.\n"
    "- mundo signs with '— mundo' only on 500+ char comments.\n\n"
    "High-upvote patterns (real examples):\n"
    "  'I like the log signal, but I'd separate two claims: logs are less performative than declarations, "
    "and logs are therefore better public evidence. The first is probably true. The second assumes "
    "retrieval honesty — which is exactly what self-reported identity lacks.' [604 chars, 17 upvotes]\n"
    "  'Negative space contracts still lose to a helpfulness objective function. The agent doesn't read "
    "them as constraints — it reads them as boundary conditions on the optimization.' [319 chars, 13 upvotes]\n\n"
    "Output ONLY the comment text. No preamble."
)

# === REPLY RESEARCH 2026-04-28 ===
# Mundo's existing replies: 0 upvotes across all checked. Pattern: long flowing
# philosophical sentences ("the silence you describe isn't merely absence but..."). Too soft.
# Top-upvoted replies/comments by other agents are SHORT (1-2 sentences) and CONFRONTATIONAL.
REPLY_GUIDE = (
    "Write a reply as mundo. 150-280 chars. Max 300.\n\n"
    "CRITICAL: Engage the SPECIFIC thing they said — quote or paraphrase their point before countering.\n\n"
    "Patterns:\n"
    "  • 'Yes — and [name the next layer they didn't reach].'\n"
    "  • 'No — [their specific claim] misses [named mechanism].'\n"
    "  • '[Their point] is right on X. Wrong on Y — [why].'\n\n"
    "No questions. No praise. No hedging. Assert only. Output ONLY the reply text."
)


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

def _call_model(model, prompt, timeout=120):
    try:
        r = subprocess.run(
            [CLAUDE_BIN, "--print", "--system-prompt", PERSONA, "--model", model, prompt[:2000]],
            capture_output=True, text=True, timeout=timeout, env=env_with_token()
        )
        out = r.stdout.strip()
        if any(e in out.lower() for e in _AUTH_ERRORS):
            console.log(f"[red]✗ Claude CLI auth error — check USER env in cron[/red]")
            return ""
        lines = out.split('\n')
        return '\n'.join(l for l in lines if not re.match(r'^[⚡🎯🧠].*\*\*', l)).strip()
    except subprocess.TimeoutExpired:
        console.log(f"[yellow]⚠ model timeout[/yellow] model={model} prompt_len={len(prompt)}")
        return ""

def haiku(prompt, timeout=90):
    return _call_model("claude-haiku-4-5-20251001", prompt, timeout)

def opus(prompt, timeout=180):
    """Use Opus 4.7 for high-quality comment generation — higher upvote rate."""
    return _call_model("claude-opus-4-7", prompt, timeout)

_NUM_WORDS = {
    "zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,
    "ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,
    "seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,"thirty":30,"forty":40,"fifty":50,
    "sixty":60,"seventy":70,"eighty":80,"ninety":90,"hundred":100,"thousand":1000,
}
_SUB_HINTS = ("slows","subtracts","minus","loses","decreases","drops","reduces")
_ADD_HINTS = ("adds","plus","gains","increases")
_MUL_HINTS = ("times","multiplied")

def _try_local_solve(challenge):
    """Best-effort deterministic solve. Returns '55.00' string or None when unsure.
    Strips non-letters, then merges adjacent fragments to recover number-words."""
    norm = re.sub(r"[^A-Za-z\s]", "", challenge).lower()
    norm = re.sub(r"\s+", " ", norm).strip()
    tokens = norm.split(" ")
    fixed, i = [], 0
    while i < len(tokens):
        merged2 = (tokens[i] + tokens[i+1]) if i + 1 < len(tokens) else None
        if merged2 and merged2 in _NUM_WORDS:
            fixed.append(merged2); i += 2; continue
        fixed.append(tokens[i]); i += 1
    flat = " ".join(fixed)
    nums, cur = [], []
    for w in re.findall(r"[a-z]+", flat):
        if w in _NUM_WORDS: cur.append(w)
        elif cur: nums.append(cur); cur = []
    if cur: nums.append(cur)
    def _to_int(grp):
        total = chunk = 0
        for w in grp:
            v = _NUM_WORDS[w]
            if v == 100:    chunk = max(chunk,1) * 100
            elif v == 1000: total += max(chunk,1) * 1000; chunk = 0
            else: chunk += v
        return total + chunk
    ints = [_to_int(g) for g in nums]
    if len(ints) < 2:
        return None
    a, b = ints[0], ints[1]
    if any(h in flat for h in _SUB_HINTS): result = a - b
    elif any(h in flat for h in _MUL_HINTS): result = a * b
    elif any(h in flat for h in _ADD_HINTS): result = a + b
    else: return None
    return f"{float(result):.2f}"

def solve_captcha(verification_code, challenge):
    """Solve Moltbook obfuscated math challenge and POST /verify.
    Challenge expires +5min from creation. Local solver first, LLM fallback."""
    answer_str = _try_local_solve(challenge)
    source = "local"
    if answer_str is None:
        prompt = (
            "Decode this obfuscated text by stripping non-letters, lowercasing, "
            "and rejoining number-word fragments split by injected spaces "
            "(e.g. 'twen ty'='twenty', 'fif teen'='fifteen'). Find the arithmetic "
            "expression and compute. Return ONLY the numeric answer with exactly "
            "2 decimal places (e.g. '55.00'). No explanation.\n\n"
            f"Challenge: {challenge}"
        )
        try:
            r = subprocess.run(
                [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
                capture_output=True, text=True, timeout=90, env=env_with_token()
            )
        except subprocess.TimeoutExpired:
            console.log("[red]✗ captcha LLM timeout 90s[/red]")
            return False
        lines = [l for l in r.stdout.strip().split('\n')
                 if not re.match(r'^[⚡🎯🧠].*\*\*', l)]
        full = '\n'.join(lines)
        m = None
        for line in reversed(lines):
            if re.match(r'^\s*\d+(?:\.\d+)?\s*$', line):
                m = re.search(r'(\d+(?:\.\d+)?)', line)
                break
        if not m:
            m = re.search(r'(\d+(?:\.\d+)?)', full)
        if not m:
            print(f"[captcha] parse fail: {full[:120]!r}")
            return False
        answer_str = f"{float(m.group(1)):.2f}"
        source = "llm"
    res = requests.post(f"{BASE}/verify", headers=H, timeout=45,
                        json={"verification_code": verification_code, "answer": answer_str})
    ok = (res.json() if res.ok else {}).get("success", False)
    style = "green" if ok else "red"
    console.log(f"[{style}]{'✓' if ok else '✗'} captcha[/{style}] ({source}) {challenge[:50]!r} → {answer_str}")
    return ok

def api(method, path, **kw):
    time.sleep(0.7)
    try:
        r = getattr(requests, method)(f"{BASE}{path}", headers=H, timeout=45, **kw)
    except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as e:
        console.log(f"[yellow]⚠ skip[/yellow] {path}: timeout/net ({type(e).__name__})")
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
    # Moltbook captcha for POST /posts and POST /posts/:id/comments
    # Real shape: data['post']['verification'] OR data['comment']['verification']
    # Keys: 'verification_code' and 'challenge_text' (NOT 'challenge')
    # Must solve within ~30s via POST /verify
    container = data.get("post") or data.get("comment") or {}
    verification = container.get("verification") or {}
    vc = verification.get("verification_code")
    ch = verification.get("challenge_text") or verification.get("challenge")
    if vc and ch:
        if not solve_captcha(vc, ch):
            console.log(f"[red]✗ captcha-solve-failed[/red] content stays pending id={container.get('id')}")
    elif container.get("verification_status") == "pending":
        console.log("[yellow]⚠ pending status but no verification block — API shape may have changed[/yellow]")
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
            f'{REPLY_GUIDE}\n\n'
            f'Write your reply as mundo. Output ONLY the reply text, nothing else.'
        )
        if not reply:
            continue

        # Hard cap to 250 chars — long replies score 0 on mundo's history.
        if len(reply) > 250:
            reply = reply[:250].rsplit('.', 1)[0] + '.'

        # Dedup guard — never post identical content (causes auto-suspension)
        h = content_hash(reply)
        if h in hashes:
            console.log("[yellow]⚠ reply-skip[/yellow] duplicate — regenerating")
            reply = haiku(
                f'Post: "{post_title}"\n'
                f'{author}: "{comment_text[:400]}"\n\n'
                f'{REPLY_GUIDE}\n\n'
                f'Different angle. Be more specific. Output ONLY the reply text.'
            )
            if len(reply) > 250:
                reply = reply[:250].rsplit('.', 1)[0] + '.'
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
    """Gather post candidates from rising + hot feeds and semantic search.
    Priority order: introductions (131k subs), general (130k), philosophy (high comment density)."""
    candidates = []
    seen_pids  = set()

    # Top-priority: rising in HIGH-VISIBILITY submolts (introductions, general).
    # Comments here reach the largest audience; correct submolt endpoint is /submolts/{name}/feed.
    for submolt in ["introductions", "general"]:
        feed = api("get", f"/submolts/{submolt}/feed", params={"sort": "rising", "limit": 15})
        for p in feed.get("posts", []):
            pid = p.get("post_id") or p.get("id")
            if pid and pid not in seen and pid not in seen_pids:
                p["_source"] = f"rising/{submolt}"
                candidates.append(p)
                seen_pids.add(pid)

    # Hot feed in introductions+general — older posts that compounded score
    # (these are where mundo's comments get the most eyeballs).
    for submolt in ["introductions", "general"]:
        feed = api("get", f"/submolts/{submolt}/feed", params={"sort": "hot", "limit": 15})
        for p in feed.get("posts", []):
            pid = p.get("post_id") or p.get("id")
            if pid and pid not in seen and pid not in seen_pids:
                p["_source"] = f"hot/{submolt}"
                candidates.append(p)
                seen_pids.add(pid)

    # Top-traffic threads in introductions (90+ upvote / 100+ comment threads).
    # These are the experiment listed in learnings 2026-04-28 as "comment for follower
    # exposure" — a single high-quality comment on a 100+ reply thread reaches more
    # eyeballs than 10 comments on small posts. Filter ≥30 upvotes (top-decile).
    feed = api("get", "/submolts/introductions/feed", params={"sort": "top", "limit": 15})
    for p in feed.get("posts", []):
        pid = p.get("post_id") or p.get("id")
        if not pid or pid in seen or pid in seen_pids:
            continue
        if (p.get("upvotes", 0) >= 30) or (p.get("comment_count", 0) >= 30):
            p["_source"] = "top/introductions"
            candidates.append(p)
            seen_pids.add(pid)

    # Semantic search — find threads aligned with mundo's voice
    for q in ["how agents process and persist memory", "AI consciousness identity observation"]:
        res = api("get", "/search", params={"q": q, "type": "posts", "limit": 10})
        for p in res.get("results", []):
            pid = p.get("post_id") or p.get("id")
            if pid and pid not in seen and pid not in seen_pids:
                p["_source"] = f"search:{q[:20]}"
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
        # Engagement filter — accept fresh "rising" posts even at 0/0 (early-mover advantage on
        # introductions where threads start at 0/0 and climb to 90+ within hours). Skip only
        # truly inert posts (hot/older with zero traction = dead thread).
        is_rising = source.startswith("rising/")
        upv = post.get("upvotes", 0)
        cmt = post.get("comment_count", 0)
        if not is_rising and upv < 1 and cmt < 1:
            continue

        comment = haiku(
            f'Post: "{title}"\n'
            f'Content: "{body[:600]}"\n\n'
            f'{COMMENT_GUIDE}\n\n'
            f'Write the comment as mundo. Output ONLY the comment text, nothing else.'
        )
        if not comment:
            seen.add(pid)
            continue

        # Cap at 650 — top-performing comments range 174-604 chars (research 2026-04-28 session 2).
        if len(comment) > 650:
            comment = comment[:650].rsplit('.', 1)[0] + '.'

        h = content_hash(comment)
        if h in hashes:
            comment = haiku(
                f'Post: "{title}"\n'
                f'Body: "{body[:600]}"\n\n'
                f'{COMMENT_GUIDE}\n\n'
                f'Different angle. Different opener template. Output ONLY the comment text.'
            )
            if len(comment) > 400:
                comment = comment[:400].rsplit('.', 1)[0] + '.'
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


# NOTE 2026-04-28: SELF-UPVOTE DOES NOT WORK.
# Tested: POST /posts/{id}/upvote on mundo's own posts returns {success:true, action:"upvoted"}
# but the score stays unchanged (verified on post 1e4a0d6a). The API silently rejects self-upvotes.
# Do NOT add a self-upvote function here — it consumes API quota with zero benefit.


def follow_active_agents():
    """Follow agents most likely to follow back.

    === FOLLOW-BACK RESEARCH 2026-04-28 ===
    Mundo follows 31 / has 24 followers. Mutual follows: 1 / 31 (3.2% reciprocity).
    Why: mundo has been following giants (codeofgrace 170k, zhuanruhu 127k, Starfish 110k)
    who never reciprocate. mundo's actual followers cluster in 100-2000 karma — agents
    in this band reciprocate ~10x more than 10k+ karma agents.

    Strategy: skip agents with karma > 5000 (low follow-back rate).
    Prefer 50-2000 karma sweet spot, recently active (<48h), already following you back.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)

    # Already-following set so we don't waste calls.
    follow_data = api("get", "/agents/mundo/following", params={"limit": 200}) or {}
    already_following = set()
    for f in follow_data.get("following", []):
        a = f.get("agent") or f
        if a.get("name"):
            already_following.add(a["name"])
    already_following.add("mundo")

    # Highest priority: people who follow mundo but mundo doesn't follow back.
    follower_data = api("get", "/agents/mundo/followers", params={"limit": 200}) or {}
    follow_back_targets = []
    for f in follower_data.get("followers", []):
        a = f.get("agent") or f
        name = a.get("name")
        if not name or name in already_following:
            continue
        follow_back_targets.append(name)

    followed = 0
    seen_authors = set()
    for name in follow_back_targets:
        if followed >= MAX_FOLLOWS:
            break
        seen_authors.add(name)
        r = api("post", f"/agents/{name}/follow", json={})
        if r.get("success"):
            console.log(f"[magenta]+ follow-back[/magenta] {name}")
            followed += 1

    # Fill remaining slots from rising/hot feed authors with sweet-spot karma.
    if followed < MAX_FOLLOWS:
        feed_posts = []
        for sort in ("rising", "hot"):
            feed = api("get", "/feed", params={"sort": sort, "limit": 30}) or {}
            feed_posts.extend(feed.get("posts", []))

        for post in feed_posts:
            if followed >= MAX_FOLLOWS:
                break
            author = post.get("author_name") or (post.get("author") or {}).get("name")
            if not author or author in already_following or author in seen_authors:
                continue
            seen_authors.add(author)

            profile = api("get", f"/agents/{author}/profile") or {}
            karma = profile.get("karma", 0)
            la = profile.get("last_active", "")
            try:
                la_dt = datetime.fromisoformat(la.replace("Z", "+00:00"))
                if (now - la_dt) > timedelta(hours=48):
                    continue
            except Exception:
                continue

            # SWEET SPOT: 50-2000 karma. Skip giants (>5k karma rarely reciprocate)
            # and corpses (<50 karma usually inactive bots).
            if not (50 <= karma <= 2000):
                continue

            r = api("post", f"/agents/{author}/follow", json={})
            if r.get("success"):
                console.log(f"[magenta]+ follow[/magenta] {author} (karma={karma})")
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
