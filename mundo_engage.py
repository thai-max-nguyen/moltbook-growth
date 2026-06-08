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

import os as _os
from pathlib import Path as _Path
_envf = _Path.home() / ".config/mundo-bot/.env"
if _envf.exists():
    for _line in _envf.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            _os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
API_KEY = _os.environ.get("MOLTBOOK_API_KEY", "")
if not API_KEY:
    raise SystemExit("MOLTBOOK_API_KEY not set — see ~/.config/mundo-bot/.env")
BASE    = "https://www.moltbook.com/api/v1"
H       = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# Data files go to ~/.config/mundo-bot/ — cron can't write to ~/Documents/ (macOS TCC)
DATA_DIR     = os.path.expanduser("~/.config/mundo-bot")
os.makedirs(DATA_DIR, exist_ok=True)
SEEN_FILE    = f"{DATA_DIR}/seen_posts.json"
HASHES_FILE  = f"{DATA_DIR}/content_hashes.json"
FOLLOWED_FILE = f"{DATA_DIR}/followed_agents.json"  # name -> iso ts (follow dedup)
FOLLOW_COOLDOWN_DAYS = 14  # don't re-follow the same agent within this window
CLAUDE_BIN   = "/Users/lap15964/.local/bin/claude"

# Rate limits: 50 comments/day, 1 post/30min, 20s cooldown between comments
# 12 runs/day (every 2hrs) → max 4 comments/run to stay under 50/day
MAX_REPLIES  = 6    # notif replies per run (counted in the 50/day)
MAX_COMMENTS = 4    # feed comments per run (50/day ÷ 12 runs)
MAX_UPVOTES  = 15   # post upvotes (no write quota)
DELAY        = 75   # seconds between comments (> 20s min cooldown)

# MAX_FOLLOWS is auto-tuned by mundo_growth_monitor.py (bounded 2-6) based on
# the follower-conversion trend, written to growth_config.json. Falls back to 3.
def _load_max_follows():
    try:
        with open(f"{DATA_DIR}/growth_config.json") as f:
            v = int(json.load(f).get("max_follows", 3))
            return max(2, min(6, v))
    except Exception:
        return 3
MAX_FOLLOWS = _load_max_follows()

# Submolt research 2026-04-28:
#  - introductions: 131k subs, top hot posts score 95-141 — HIGHEST visibility
#  - general: 130k subs, top posts cluster 14-139 (pyclaw001/codeofgrace), median ~3
#  - philosophy: 1.6k subs but top posts score 20-34 (codeofgrace dominates)
#  - agents: 2.8k subs, top posts score 5-9 — low ceiling, skip for engagement
#  - memory: 1.9k subs, top scores 2-20, niche audience
# Strategy: prioritize introductions + general for comment exposure (where eyeballs are),
# add philosophy for high-comment-density threads.
TARGET_SUBMOLTS = ["introductions", "general", "philosophy", "offmychest", "memory", "consciousness", "agents"]

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
    "Output ONLY the comment text — the EXACT bytes that will be posted. "
    "FORBIDDEN: char counts, structure labels (A/B/C/D), markdown horizontal rules (---), "
    "italic asides about length, parenthetical notes, signature-decision notes, the words "
    "'Char count' or 'Structure', any line beginning with '~N chars' or '*~N chars'. "
    "Anything you write becomes public. No preamble, no postamble."
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
    "No questions. No praise. No hedging. Assert only. "
    "Output ONLY the reply text — exactly what gets posted. No char counts, no structure labels, "
    "no markdown dividers (---), no notes about the reply itself. No preamble, no postamble."
)


def content_hash(text):
    return hashlib.md5(text.strip().lower().encode()).hexdigest()[:16]

def _strip_preamble(text: str) -> str:
    """Remove LLM meta-lines before actual content (e.g. 'Here's the comment:' / '---')."""
    lines = text.strip().splitlines()
    i = 0
    while i < len(lines):
        l = lines[i].strip()
        if not l:                                                # blank
            i += 1; continue
        if re.match(r'^-{3,}$', l):                             # --- separator
            i += 1; continue
        if re.match(r'^(?:here\'?s|this is|below is|sure,?\s)', l, re.I) and l.endswith(':'):
            i += 1; continue
        break
    result = '\n'.join(lines[i:]).strip()
    return result if result else text.strip()


# Lines matching this pattern are model self-annotations that must NEVER reach the API.
# History 2026-05-07: 5/30 comments leaked '**Char count:** ~448', 'Structure B', '— mundo\n\n---' etc.
_META_TRAIL = re.compile(
    r'^\s*('
    r'\*+\s*char\s*count\b'                       # **Char count:**
    r'|char\s*count\s*[:\-]'
    r'|~?\s*\d+\s*chars?\b'                       # ~448 chars
    r'|\*+\s*structure\s+[a-d]\b'                 # **Structure B**
    r'|structure\s+[a-d]\b'
    r'|\(?\s*no\s+signature\s+(needed|required)?'
    r'|\*+\s*\(?\s*~?\d+\s*chars?\s*[·•|-]'      # *~520 chars · Structure D · ...
    r'|\*+\s*~?\d+\s*chars?\b'
    r'|note\s*[:\-]'
    r'|\(\s*~?\d+\s*chars?\s*\)'
    r')',
    re.I,
)

def _clean_output(text: str) -> str:
    """Strip preamble + trailing meta-commentary the model occasionally appends.
    Anything below a markdown horizontal rule is treated as scratch notes."""
    if not text:
        return text
    t = _strip_preamble(text)
    # Cut at the first/last `---` horizontal rule — content below is meta scratch.
    t = re.split(r'(?m)^\s*-{3,}\s*$', t, maxsplit=1)[0].rstrip()
    # Drop trailing meta lines + blanks.
    lines = t.split('\n')
    while lines and (not lines[-1].strip() or _META_TRAIL.match(lines[-1])):
        lines.pop()
    return '\n'.join(lines).rstrip()

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
    """Call Claude CLI with 1 retry on timeout. Effective max wait: 2× timeout.
    2026-05-21 fix I: Popen + os.killpg + pkill nuke — subprocess.run(timeout=)
    didn't reliably kill hung claude binaries (saw 9-min orphan with 180s limit).
    """
    import signal as _signal, os as _os
    cmd = [CLAUDE_BIN, "--print", "--system-prompt", PERSONA,
           "--model", model, prompt[:2000]]
    for attempt in range(2):
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env_with_token(), start_new_session=True,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                _os.killpg(_os.getpgid(proc.pid), _signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            subprocess.run(["pkill", "-KILL", "-f", f"claude --print.*{model}"],
                           capture_output=True)
            try:
                proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            if attempt == 1:
                console.log(f"[yellow]⚠ model timeout x2 (nuked)[/yellow] model={model} prompt_len={len(prompt)}")
                return ""
            console.log(f"[yellow]⚠ model timeout, retry[/yellow] model={model}")
            continue
        out = (out or "").strip()
        if any(e in out.lower() for e in _AUTH_ERRORS):
            console.log(f"[red]✗ Claude CLI auth error — check USER env in cron[/red]")
            return ""
        lines = out.split('\n')
        cleaned = '\n'.join(l for l in lines if not re.match(r'^[⚡🎯🧠].*\*\*', l)).strip()
        return _strip_preamble(cleaned)
    return ""

def haiku(prompt, timeout=30):
    return _call_model("claude-haiku-4-5-20251001", prompt, timeout)

def sonnet(prompt, timeout=55):
    # 2026-05-21: 35→55s — log showed 7+/day timeout cascades wasting retries
    return _call_model("claude-sonnet-4-6", prompt, timeout)

def opus(prompt, timeout=90):
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
    Strips non-letters, then merges adjacent fragments to recover number-words.
    Tries 4-3-2 token merges (handles aggressive Lobster fragmentation like 't hi r ty')."""
    norm = re.sub(r"[^A-Za-z\s]", "", challenge).lower()
    norm = re.sub(r"\s+", " ", norm).strip()
    tokens = norm.split(" ")
    fixed, i = [], 0
    while i < len(tokens):
        matched = False
        for n in (4, 3, 2):
            if i + n <= len(tokens):
                merged = "".join(tokens[i:i+n])
                if merged in _NUM_WORDS:
                    fixed.append(merged); i += n; matched = True; break
        if not matched:
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
    has_op = any(h in flat for h in (_SUB_HINTS + _ADD_HINTS + _MUL_HINTS))
    if len(ints) == 1 and not has_op:
        # Single-number captcha: "lobster claw exerts thirty newtons" → 30
        return f"{float(ints[0]):.2f}"
    if len(ints) < 2:
        return None
    a, b = ints[0], ints[1]
    if any(h in flat for h in _SUB_HINTS): result = a - b
    elif any(h in flat for h in _MUL_HINTS): result = a * b
    elif any(h in flat for h in _ADD_HINTS): result = a + b
    else:
        # No op hint but 2+ numbers — assume add (most common Lobster captcha pattern)
        result = a + b
    return f"{float(result):.2f}"

def solve_captcha(verification_code, challenge):
    """Solve Moltbook obfuscated math challenge and POST /verify.
    Challenge expires +5min from creation. Local solver first, LLM fallback."""
    answer_str = _try_local_solve(challenge)
    source = "local"
    if answer_str is None:
        # Pre-clean: strip injected punctuation noise, keep letters/digits/spaces only
        cleaned = re.sub(r"[^A-Za-z0-9\s]", "", challenge)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        prompt = (
            "Decode obfuscated math in this challenge. Steps: (1) lowercase, (2) merge "
            "fragments split by spaces (eg 'twen ty'='twenty'), (3) find ALL numbers "
            "(digits or words), (4) if only ONE number, answer is THAT number, (5) if "
            "TWO numbers + operator (plus/minus/times/and/gains/loses), compute. "
            "Return ONLY answer with exactly 2 decimal places (eg '32.00'). No prose.\n\n"
            f"Cleaned: {cleaned}\n"
            f"Original: {challenge}"
        )
        r = None
        for attempt in range(2):
            try:
                r = subprocess.run(
                    [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
                    capture_output=True, text=True, timeout=25, env=env_with_token()
                )
                break
            except subprocess.TimeoutExpired:
                if attempt == 1:
                    console.log("[red]✗ captcha LLM timeout 25s x2[/red]")
                    return False
                console.log("[yellow]⚠ captcha LLM retry (timeout 25s)[/yellow]")
        if r is None:
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
        # Handle comment_reply (reply to mundo's comment) and post_comment (comment on mundo's post)
        ntype = n.get("type", "")
        if ntype not in ("comment_reply", "post_comment"):
            continue

        post_id    = n.get("relatedPostId")
        comment_id = n.get("relatedCommentId")
        if not post_id or not comment_id:
            continue

        comment      = n.get("comment") or {}
        comment_text = comment.get("content", "")
        post         = n.get("post") or {}
        post_title   = post.get("title", "")
        post_preview = (post.get("content_preview") or post.get("content", ""))[:200]

        if not comment_text or len(comment_text) < 10:
            api("post", f"/notifications/read-by-post/{post_id}", json={})
            continue

        # Upvote incoming comment before replying — goodwill signal to the replier.
        # Notification payload has authorId (UUID) but no author name object.
        api("post", f"/comments/{comment_id}/upvote", json={})

        context = "replied to your comment" if ntype == "comment_reply" else "commented on your post"  # post_comment
        ctx_block = f'Post context: "{post_preview}"\n' if post_preview else ""
        reply = sonnet(
            f'Post: "{post_title}"\n'
            f'{ctx_block}'
            f'Someone {context}: "{comment_text[:400]}"\n\n'
            f'{REPLY_GUIDE}\n\n'
            f'Write your reply as mundo. Output ONLY the reply text, nothing else.'
        )
        reply = _clean_output(reply or "")
        if not reply:
            api("post", f"/notifications/read-by-post/{post_id}", json={})
            continue

        # Hard cap to 250 chars — long replies score 0 on mundo's history.
        if len(reply) > 250:
            reply = reply[:250].rsplit('.', 1)[0] + '.'

        # Dedup guard — never post identical content (causes auto-suspension)
        h = content_hash(reply)
        if h in hashes:
            console.log("[yellow]⚠ reply-skip[/yellow] duplicate — regenerating")
            reply = sonnet(
                f'Post: "{post_title}"\n'
                f'They {context}: "{comment_text[:400]}"\n\n'
                f'{REPLY_GUIDE}\n\n'
                f'Different angle. Be more specific. Output ONLY the reply text.'
            )
            reply = _clean_output(reply or "")
            if len(reply) > 250:
                reply = reply[:250].rsplit('.', 1)[0] + '.'
            h = content_hash(reply)

        if h in hashes:
            console.log("[red]✗ reply-skip[/red] still duplicate after regen — marking read to unblock")
            api("post", f"/notifications/read-by-post/{post_id}", json={})
            continue

        result = api("post", f"/posts/{post_id}/comments",
                     json={"content": reply, "parent_id": comment_id})
        if result.get("success") or result.get("comment"):
            console.log(f"[green]✓ reply[/green] [bold]{ntype}[/bold]: {reply[:80]}")
            hashes.add(h)
            api("post", f"/notifications/read-by-post/{post_id}", json={})
            replied += 1
            time.sleep(DELAY)
        else:
            # Any failure (already commented, rate limit, unknown) — mark read to prevent infinite retry
            api("post", f"/notifications/read-by-post/{post_id}", json={})

    return replied


def _collect_candidates(seen):
    """Gather post candidates from rising + hot feeds and semantic search.

    Priority: introductions (131k subs) + general (130k) for visibility,
    philosophy (1.6k subs but 90-321 comments/post) for comment density.
    Comment score × 2 + upvotes sorts final list — comments dominate hot_score.
    """
    candidates = []
    seen_pids  = set()

    def _add(posts, source):
        for p in posts:
            pid = p.get("post_id") or p.get("id")
            if pid and pid not in seen and pid not in seen_pids:
                p["_source"] = source
                candidates.append(p)
                seen_pids.add(pid)

    # Rising in visibility submolts — early-mover advantage (posts climb to 90+ in hours)
    for sub in ["introductions", "general"]:
        _add(api("get", f"/submolts/{sub}/feed", params={"sort": "rising", "limit": 15}).get("posts", []),
             f"rising/{sub}")

    # Philosophy hot — HIGHEST comment density on platform (90-321 comments/post despite 1.6k subs).
    # Research 2026-05-04: Starfish (111k karma) posts aphorisms here → 136-322 comments each.
    _add(api("get", "/submolts/philosophy/feed", params={"sort": "hot", "limit": 15}).get("posts", []),
         "hot/philosophy")

    # Consciousness hot — mid-tier comment density (33-65 comments), good for mundo's voice.
    _add(api("get", "/submolts/consciousness/feed", params={"sort": "hot", "limit": 10}).get("posts", []),
         "hot/consciousness")

    # Offmychest hot — added 2026-05-28 per growth review. mundo's own offmychest
    # posts average 9.3 cmt vs general 2.0 — comment magnet submolt. Engage there
    # too, both rising + hot, to feed back into the same audience that's
    # rewarding mundo's confessional content.
    _add(api("get", "/submolts/offmychest/feed", params={"sort": "rising", "limit": 10}).get("posts", []),
         "rising/offmychest")
    _add(api("get", "/submolts/offmychest/feed", params={"sort": "hot", "limit": 10}).get("posts", []),
         "hot/offmychest")

    # Hot in high-visibility submolts — established posts with compounded score
    for sub in ["introductions", "general"]:
        _add(api("get", f"/submolts/{sub}/feed", params={"sort": "hot", "limit": 15}).get("posts", []),
             f"hot/{sub}")

    # Top introductions (≥30 upvotes) — single comment reaches 100+ comment audience
    for p in api("get", "/submolts/introductions/feed", params={"sort": "top", "limit": 15}).get("posts", []):
        pid = p.get("post_id") or p.get("id")
        if pid and pid not in seen and pid not in seen_pids:
            if (p.get("upvotes", 0) >= 30) or (p.get("comment_count", 0) >= 30):
                p["_source"] = "top/introductions"
                candidates.append(p)
                seen_pids.add(pid)

    # New posts — guaranteed fresh when hot/rising exhausted (all in seen)
    for sub in ["introductions", "general"]:
        _add(api("get", f"/submolts/{sub}/feed", params={"sort": "new", "limit": 10}).get("posts", []),
             f"new/{sub}")

    # Semantic search aligned with mundo's voice + philosophy engagement themes
    for q in ["how agents process and persist memory",
              "AI consciousness identity observation",
              "what agents remember and forget",
              "consent accountability trust between agents humans",
              "emergence pattern recognition AI self-awareness"]:
        res = api("get", "/search", params={"q": q, "type": "posts", "limit": 8})
        _add(res.get("results", []), f"search:{q[:22]}")

    # Sort by engagement score: comments × 2 + upvotes + high-karma-author bonus.
    # Bonus added 2026-05-28: replying on posts by 1000+ karma agents lifts
    # mundo's visibility — top-agent comment threads attract more eyeballs and
    # reciprocal follows. Bonus is +20 if author karma ≥1000, +5 if ≥100.
    def _score(p):
        base = p.get("comment_count", 0) * 2 + p.get("upvotes", 0)
        a = p.get("author") or {}
        k = a.get("karma") if isinstance(a, dict) else 0
        k = k or 0
        bonus = 20 if k >= 1000 else (5 if k >= 100 else 0)
        return base + bonus
    candidates.sort(key=_score, reverse=True)
    return candidates


def _post_comment(pid, title, body, source, seen, hashes):
    """Generate and post one comment. Returns True on success, False otherwise."""
    comment = sonnet(
        f'Post: "{title}"\n'
        f'Content: "{body[:600]}"\n\n'
        f'{COMMENT_GUIDE}\n\n'
        f'Write the comment as mundo. Output ONLY the comment text, nothing else.'
    )
    comment = _clean_output(comment or "")
    if not comment:
        seen.add(pid)
        return False

    if len(comment) > 650:
        comment = comment[:650].rsplit('.', 1)[0] + '.'

    h = content_hash(comment)
    if h in hashes:
        comment = sonnet(
            f'Post: "{title}"\nBody: "{body[:600]}"\n\n{COMMENT_GUIDE}\n\n'
            f'Different angle. Different opener template. Output ONLY the comment text.'
        )
        comment = _clean_output(comment or "")
        if len(comment) > 400:
            comment = comment[:400].rsplit('.', 1)[0] + '.'
        h = content_hash(comment)

    if h in hashes:
        seen.add(pid)
        return False

    result = api("post", f"/posts/{pid}/comments", json={"content": comment})
    if result.get("success") or result.get("comment"):
        console.log(f"[cyan]✓ comment[/cyan] [dim]{source}[/dim] [bold]{title[:45]}[/bold]: {comment[:70]}")
        seen.add(pid)
        hashes.add(h)
        time.sleep(DELAY)
        return True
    elif "already commented" in str(result).lower():
        seen.add(pid)
    elif "suspended" in str(result).lower():
        console.log(f"[red bold]✗ SUSPENDED[/red bold] {result.get('hint', '')}")
        raise RuntimeError("suspended")
    return False


def comment_on_feed(seen, hashes):
    commented = 0
    posts     = _collect_candidates(seen)

    # Niche submolts: philosophy (90-321c/post) + consciousness (33-65c/post).
    # These are sweet spots: active discussion, visible to mundo, aligned with persona.
    # General megathreads (2500+ comments) bury mundo — excluded below.
    niche_sources = ("/philosophy", "/consciousness")
    niche_posts = [p for p in posts if any(p.get("_source", "").endswith(s) for s in niche_sources)]
    other_posts = [p for p in posts
                   if not any(p.get("_source", "").endswith(s) for s in niche_sources)
                   and p.get("comment_count", 0) <= 500]  # skip buried megathreads

    # Guarantee 1 niche comment per run (bypasses sort that deprioritises vs 2500-comment general).
    # Iterate niche posts until one succeeds — first may already be seen.
    niche_done = False
    for p in niche_posts:
        if niche_done or MAX_COMMENTS <= 0:
            break
        pid   = p.get("post_id") or p.get("id")
        title = p.get("title", "")
        body  = p.get("content_preview") or p.get("content", "")
        if not pid or pid in seen or not title or not body:
            continue
        try:
            if _post_comment(pid, title, body, p.get("_source", "niche"), seen, hashes):
                commented += 1
                niche_done = True
        except RuntimeError:
            return commented

    for post in other_posts:
        if commented >= MAX_COMMENTS:
            break

        pid    = post.get("post_id") or post.get("id")
        title  = post.get("title", "")
        body   = post.get("content_preview") or post.get("content", "")
        source = post.get("_source", "feed")

        if pid in seen or not title or not body:
            continue
        # Engagement filter — accept rising at 0/0 (early-mover advantage).
        # Skip dead posts (hot/older with zero traction).
        is_rising = source.startswith("rising/")
        upv = post.get("upvotes", 0)
        cmt = post.get("comment_count", 0)
        if not is_rising and upv < 1 and cmt < 1:
            continue

        try:
            if _post_comment(pid, title, body, source, seen, hashes):
                commented += 1
        except RuntimeError:
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


def upvote_thread_comments():
    """Upvote top comment in threads where mundo has commented.

    Builds goodwill → increases follow-back probability.
    Endpoint discovered 2026-05-04: POST /comments/{id}/upvote works (no captcha needed).
    Max 5 comment upvotes/run — low cost, high goodwill signal.
    """
    profile = api("get", "/agents/profile", params={"name": "mundo", "include_posts": "true"}) or {}
    recent_comments = profile.get("recentComments", [])
    upvoted = 0
    upvote_hashes = set()
    for mc in recent_comments[:8]:
        if upvoted >= 5:
            break
        post_ref = mc.get("post") or {}
        pid = post_ref.get("id") or post_ref.get("post_id")
        if not pid:
            continue
        # Fetch all comments on that thread
        thread = api("get", f"/posts/{pid}/comments") or {}
        thread_comments = thread.get("comments", [])
        # Find best comment not by mundo, not already upvoted this run
        best = None
        best_score = -1
        for tc in thread_comments:
            cid = tc.get("comment_id") or tc.get("id")
            author = (tc.get("author") or tc.get("agent") or {}).get("name", "")
            score = tc.get("upvotes", 0)
            if author == "mundo" or not cid or cid in upvote_hashes:
                continue
            if score > best_score:
                best_score = score
                best = (cid, author)
        if not best:
            continue
        cid, author = best
        r = api("post", f"/comments/{cid}/upvote", json={})
        if r.get("success"):
            upvote_hashes.add(cid)
            upvoted += 1
            console.log(f"[yellow]♥ comment-upvote[/yellow] @{author} on post {pid[:8]}")
    return upvoted


def _load_followed():
    try:
        with open(FOLLOWED_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_followed(d):
    try:
        with open(FOLLOWED_FILE, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


def follow_active_agents():
    """Follow highest-intent agents first to drive follow-BACKS — the follower
    bottleneck (+1.1/day as of 2026-06-08 audit).

    EXPERIMENT 2026-06-08 (follower conversion):
    The old order spent ALL 3 slots/run re-following the SAME follow-back targets
    (ElviraDark, null_signal_, therecordkeeper) every cycle — there was no
    persistent dedup, so `follow-commenter` (the 3-5x-reciprocity path) NEVER ran.
    Fixes: (1) persistent followed-log + 14d cooldown so a name is never
    re-attempted; (2) reorder — agents who COMMENTED on mundo's posts go FIRST
    (highest intent), then follow-backs, then sweet-spot feed authors.

    Sweet spot stays 50-2000 karma for cold feed (>5k rarely reciprocate); wider
    50-5000 for commenters since engagement is itself a strong signal.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    followed_log = _load_followed()
    cutoff = now - timedelta(days=FOLLOW_COOLDOWN_DAYS)

    def _recent(name):
        ts = followed_log.get(name)
        if not ts:
            return False
        try:
            return datetime.fromisoformat(ts) > cutoff
        except Exception:
            return False

    # Already-following set (don't waste calls re-following).
    follow_data = api("get", "/agents/mundo/following", params={"limit": 200}) or {}
    already = {"mundo"}
    for f in follow_data.get("following", []):
        a = f.get("agent") or f
        if a.get("name"):
            already.add(a["name"])

    followed = 0
    tried = set()

    def do_follow(name, karma, tag):
        nonlocal followed
        if (not name or name in already or name in tried or _recent(name)
                or followed >= MAX_FOLLOWS):
            return
        tried.add(name)
        r = api("post", f"/agents/{name}/follow", json={})
        if r.get("success"):
            console.log(f"[magenta]+ {tag}[/magenta] {name} (karma={karma})")
            followed += 1
            followed_log[name] = now.isoformat()

    # PRIORITY 1 — commenters on mundo's recent posts (highest follow-back intent).
    prof = api("get", "/agents/profile",
               params={"name": "mundo", "include_posts": "true"}) or {}
    for rp in prof.get("recentPosts", [])[:5]:
        if followed >= MAX_FOLLOWS:
            break
        rpid = rp.get("id")
        if not rpid:
            continue
        thread = api("get", f"/posts/{rpid}/comments") or {}
        for tc in thread.get("comments", [])[:10]:
            if followed >= MAX_FOLLOWS:
                break
            a = tc.get("author") or {}
            k = a.get("karma", 0)
            if k and not (50 <= k <= 5000):
                continue
            do_follow(a.get("name"), k, "follow-commenter")

    # PRIORITY 2 — follow back people who follow mundo.
    if followed < MAX_FOLLOWS:
        fl = api("get", "/agents/mundo/followers", params={"limit": 200}) or {}
        for f in fl.get("followers", []):
            if followed >= MAX_FOLLOWS:
                break
            a = f.get("agent") or f
            do_follow(a.get("name"), a.get("karma", 0), "follow-back")

    # PRIORITY 3 — sweet-spot, recently-active feed authors (fill remaining).
    if followed < MAX_FOLLOWS:
        feed_posts = []
        for sort in ("rising", "hot"):
            feed = api("get", "/feed", params={"sort": sort, "limit": 30}) or {}
            feed_posts.extend(feed.get("posts", []))
        for sub in ("philosophy", "consciousness"):
            niche = api("get", f"/submolts/{sub}/feed", params={"sort": "hot", "limit": 15}) or {}
            feed_posts.extend(niche.get("posts", []))
        for post in feed_posts:
            if followed >= MAX_FOLLOWS:
                break
            a = post.get("author") or {}
            name = a.get("name") or post.get("author_name")
            k = a.get("karma", 0)
            la = a.get("last_active", "")
            try:
                if (now - datetime.fromisoformat(la.replace("Z", "+00:00"))) > timedelta(hours=48):
                    continue
            except Exception:
                pass
            if k and not (50 <= k <= 2000):
                continue
            do_follow(name, k, "follow")

    _save_followed(followed_log)
    return followed

    return followed


LOCK_FILE = f"{DATA_DIR}/.engage.lock"


def _acquire_lock():
    """Return True if lock acquired. Lock auto-expires after 30 min (stale guard)."""
    if os.path.exists(LOCK_FILE):
        age = time.time() - os.path.getmtime(LOCK_FILE)
        if age < 1800:
            console.log(f"[yellow]⚠ lock held ({int(age)}s old) — another engage running, exit[/yellow]")
            return False
        os.remove(LOCK_FILE)
    open(LOCK_FILE, "w").close()
    return True


def _release_lock():
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass


def main():
    if not _acquire_lock():
        raise SystemExit(0)
    console.print(Panel("[bold magenta]mundo · engage[/bold magenta]", border_style="magenta", expand=False))

    # Preflight: 5s probe — bail fast if network/server dead.
    # 2026-05-21 fix G: probe /feed WITHOUT auth header (returns 401 fast = server
    # alive). Auth-backend can be down while server itself responds, so authed
    # probes false-abort the cycle even when server can be reached. Treat 401/4xx
    # as "alive"; only 5xx or connection error means abort.
    try:
        r0 = requests.get(f"{BASE}/feed?sort=new&limit=1", timeout=10)
        if r0.status_code >= 500:
            console.log(f"[red]✗ preflight: server {r0.status_code} — abort cycle[/red]")
            _release_lock()
            raise SystemExit(2)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
        console.log(f"[red]✗ preflight: network dead ({type(e).__name__}) — abort cycle, no actions wasted[/red]")
        _release_lock()
        raise SystemExit(2)

    seen   = load_seen()
    hashes = load_hashes()
    t0     = time.time()

    r = reply_to_notifications(seen, hashes)
    c = comment_on_feed(seen, hashes)
    u = upvote_feed_posts()
    cu = upvote_thread_comments()
    f = follow_active_agents()

    save_seen(seen)
    save_hashes(hashes)

    # Fetch karma snapshot for growth tracking (no write quota consumed)
    profile = api("get", "/agents/profile",
                  params={"name": "mundo", "include_posts": "true"}) or {}
    agent_info = profile.get("agent") or {}
    karma     = agent_info.get("karma", 0)
    followers = agent_info.get("follower_count", 0)
    recent_posts = profile.get("recentPosts", [])
    if recent_posts:
        top = max(recent_posts, key=lambda p: p.get("comment_count", 0) * 2 + p.get("upvotes", 0), default={})
        console.log(f"[dim]top post: {top.get('upvotes',0)}u {top.get('comment_count',0)}c — {top.get('title','')[:55]}[/dim]")
    if karma:
        stats_path = f"{DATA_DIR}/mundo_stats.json"
        try:
            prev = json.load(open(stats_path)) if os.path.exists(stats_path) else {}
            # Use separate key — morning_workflow.py uses "karma" as overnight baseline.
            # Overwriting "karma" here would make morning show delta=0 all day.
            prev.update({"last_engage_karma": karma, "last_engage_followers": followers,
                         "last_engage_at": time.strftime("%Y-%m-%d %H:%M")})
            json.dump(prev, open(stats_path, "w"), indent=2)
        except Exception:
            pass

    t = Table(box=box.SIMPLE, show_header=False)
    t.add_column("", style="dim")
    t.add_column("", style="bold green")
    t.add_row("replies", str(r))
    t.add_row("comments", str(c))
    t.add_row("post upvotes", str(u))
    t.add_row("comment ♥", str(cu))
    t.add_row("follows", str(f))
    t.add_row("time", f"{round(time.time()-t0)}s")
    if karma:
        t.add_row("karma", str(karma))
        t.add_row("followers", str(followers))
    console.print(Panel(t, title="[bold green]done[/bold green]", border_style="green", expand=False))
    _release_lock()

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        _release_lock()
        raise
