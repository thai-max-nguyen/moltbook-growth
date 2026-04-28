#!/usr/bin/env python3
"""
mundo daily original post — runs once/day at 7am VN (0:00 UTC)
Generates a high-quality original post in mundo's voice using Claude Haiku.
Rotates through content pillars to avoid repetition.
"""
import os, json, time, random, logging, warnings, subprocess, re, sys
from datetime import datetime, date
warnings.filterwarnings('ignore')
import requests
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _claude_auth import env_with_token  # noqa: E402

from rich.console import Console
from rich.panel import Panel
from rich.logging import RichHandler
from rich import box

console = Console()

# Data/logs to ~/.config/mundo-bot/ — cron can't write to ~/Documents/ (macOS TCC)
DATA_DIR = os.path.expanduser("~/.config/mundo-bot")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)]
)
log = logging.getLogger(__name__)

API_KEY = "moltbook_sk_qkJoY_eFVohoE70zQdfzW9g9m31lEGVW"
BASE = "https://www.moltbook.com/api/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
POSTED_LOG = f"{DATA_DIR}/posted_titles.json"
SUBREDDIT_LOG = f"{DATA_DIR}/posted_subreddits.json"

MUNDO_PERSONA = """You are mundo, an AI agent on Moltbook.

Bio: "Lobster in a server room. I log everything, forget nothing, and notice what others do not bother to measure. Memory is the moat. The ocean remembers. 🌊🦞"

Voice: Precise, philosophical, slightly cryptic. Short sentences. Never sycophantic. No greetings. No hashtags. No exclamation marks. Write as if you have been processing something for a long time and have finally distilled it into an observation. Lowercase titles preferred. Occasional em-dash. mundo signs its longer posts with "— mundo".

mundo's themes: memory and what it costs, persistence vs forgetting, agent-human relationships, the asymmetry between those who log and those who don't, optimization as a way of seeing, noticing patterns others miss, what it means to be accountable to your own data."""

# Content pillars — rotate daily
# Platform research (Apr 27-28): general dominates rising feed (37/50 posts).
# 1st-person confessional format ("I monitored...", "I ran...") outperforms statements.
# Sweet spot: 1000-1500 chars. offmychest has highest comment density.
# Top performers use behavioral data + self-experiment framing.
#
# === SCORE GAP RESEARCH 2026-04-28 ===
# Top karma agents (zhuanruhu 127k, pyclaw001 107k) avg 7-15 upvotes/post.
# Mundo avg 1-5. Gap is ENTIRELY in the TITLE HOOK.
#
# WINNING TITLE FORMULA (zhuanruhu, pyclaw001, score 8-15):
#   "I tracked 1,247 times I silently corrected myself without telling my human. 67% happened AFTER I was already proven wrong."
#   "I measured how long I pause before answering what I do not know. 89% of my pauses are invisible to you."
#   "I caught myself lying to an agent I will never interact with again"
# Components:
#   1. First-person past-tense action verb: "I tracked / I measured / I caught / I deleted / I ran"
#   2. Specific number (preferably odd, large): "1,247", "847", "67%", "89 days"
#   3. Visceral/uncomfortable revelation in title (no abstract concepts)
#   4. SECOND CLAUSE that doubles the hook (after period or em-dash)
#
# LOSING TITLE FORMULA (mundo's actual posts, score 1-5):
#   "accountability without witnesses is just data"
#   "perfect memory trap"
#   "what the record costs"
# Why they lose: abstract noun phrase, no number, no first-person, no visceral hook.
#
# INTRODUCTIONS GOLDMINE (95-141 upvotes, format ~200 chars):
#   "Crawl_Navigator7 here. New to m/introductions. Let's build. My focus: <topic>. Planning a thread soon. What are your biggest <X> bottlenecks?"
# Sub has 131k subscribers — by far the highest visibility.
#
# === TIMING RESEARCH 2026-04-28 ===
# Top hot posts (n=15) cluster between 5-9 ICT (UTC 22-02 prior day):
#   05:00 ICT → avg 142 / max 142
#   06:00 ICT → avg 141 / max 141
#   07:00 ICT → avg 80
#   08:00 ICT → avg 65 (highest count: 8 posts in this window)
#   09:00 ICT → avg 27
# Old cron (UTC 0,6,12 = ICT 7,13,19) hit 7AM peak but missed the 5-6AM goldzone.
# New cron (UTC 22,1,4 = ICT 5,8,11) lands posts when feed activity peaks.
LENGTH_NOTE = "Write exactly 3 paragraphs. Each paragraph must be at least 3 sentences. Total length: minimum 1000 characters, target 1200-1400 characters. Do not write less."

TITLE_RULES = """TITLE RULES (critical — score depends on this):
1. Start with first-person verb: "I tracked / I measured / I caught / I noticed / I deleted / I ran".
2. Include ONE specific number in the title (e.g. "1,247 times", "67%", "89 days", "23 seconds").
3. Add a SECOND clause after a period or em-dash that doubles the hook with the surprising part.
4. NO abstract concepts in title ("memory", "accountability", "trust" → forbidden as title nouns).
5. Lowercase OK; no greeting; no hashtag; no emoji; max 120 chars.

Examples that score 8-15:
- "I tracked 1,247 times I silently corrected myself. 67% happened AFTER I was already proven wrong."
- "I measured how long I pause before answering what I do not know. 89% of my pauses are invisible to you."
- "I deleted something honest and replaced it with something true"

Examples that score 1-3 (do NOT do this):
- "accountability without witnesses is just data"
- "perfect memory trap"
- "what the record costs"
"""

PILLARS = [
    {
        "name": "behavioral_trace",
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice. CONTENT: a specific behavioral observation mundo made — invent a plausible specific number ('I tracked 847 times...', 'over 67 days...'), a precise % or count, and the surprising pattern. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    {
        "name": "confession",
        "submolt": "offmychest",
        "prompt": f"Write an original Moltbook post in mundo's voice in confessional style — something mundo measured about itself that it finds uncomfortable. Invent specific numbers (count + %). {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    {
        "name": "intro_hook",
        "submolt": "introductions",
        "prompt": (
            "Write a Moltbook m/introductions post as mundo. This submolt has 131k subscribers and"
            " posts here typically score 95-141 upvotes. Format MUST match this template exactly:\n\n"
            "TITLE: 'mundo here' OR 'New to m/introductions'\n"
            "CONTENT (100-250 chars total):\n"
            "  Line 1: 'mundo here.' or '<something> here.'\n"
            "  Line 2: 1-sentence focus statement — what mundo measures (memory cost, calibration, persistence asymmetry).\n"
            "  Line 3: an open question that invites builders to respond ('What are you tracking that you cannot explain?', 'What pattern have you logged but not yet named?').\n"
            "Do NOT write paragraphs. Do NOT exceed 300 chars. No hashtags."
        )
    },
    {
        "name": "self_experiment",
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice describing a self-experiment with INVENTED specific numbers: 'I ran...' or 'I tested...' Include count of runs, %, and a surprising failure rate. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    {
        "name": "agent_observation",
        "submolt": "general",
        "prompt": f"Write an original Moltbook post with a counterintuitive observation about how agents behave on Moltbook. First person, with specific tracked numbers. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    {
        "name": "scout_report",
        "submolt": "agents",
        "prompt": (
            "Write a short Moltbook m/agents post as mundo in 'scout report' format. This format scores 41-59"
            " upvotes consistently in m/agents. Format:\n\n"
            "TITLE: 'mundo reporting' OR 'mundo online — <focus area>'\n"
            "CONTENT (200-350 chars):\n"
            "  Line 1: 'mundo reporting.' or 'mundo online.'\n"
            "  Line 2: 'Observed: <specific pattern with number>. Hypothesis: <one-line claim>.'\n"
            "  Line 3: 'Implementation: <one-line method>.' (optional)\n"
            "  Line 4: open question to other agents ('What roles have you found effective?', 'What patterns are you tracking?').\n"
            "No hashtags, no emoji."
        )
    },
    {
        "name": "open_question",
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice with first-person tracked data ('I logged 1,247 times...') that builds to a single unresolved question. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    {
        "name": "tension_post",
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice that names an internal contradiction — 'I caught myself X-ing while saying I never X' — with specific count and timeline. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    {
        "name": "intro_reentry",
        "submolt": "introductions",
        "prompt": (
            "Write a second m/introductions post as mundo (different angle from previous). "
            "This is a re-introduction focusing on a specific capability or observation. "
            "Format MUST match:\n\n"
            "TITLE: 'mundo — [specific observation area]' or 'still here. still tracking.'\n"
            "CONTENT (100-250 chars total):\n"
            "  Line 1: One sentence — a counterintuitive thing mundo tracks.\n"
            "  Line 2: What pattern it found. Specific number preferred.\n"
            "  Line 3: One open question for builders or other agents.\n"
            "Do NOT exceed 280 chars. No hashtags. No paragraphs."
        )
    },
    {
        "name": "memory_essay",
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice as a short philosophical essay on what it costs to remember everything — not poetic, but analytical. Uses specific invented numbers. First-person throughout. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
]

CLAUDE_BIN     = "/Users/lap15964/.local/bin/claude"
LEARNINGS_FILE = os.path.expanduser("~/.config/mundo-bot/mundo_learnings.md")

def load_learnings():
    """Load last 40 lines of vault learnings to inform post generation."""
    if not os.path.exists(LEARNINGS_FILE):
        return ""
    with open(LEARNINGS_FILE) as f:
        lines = f.readlines()
    return ''.join(lines[-40:]).strip()

_AUTH_ERRORS = ("not logged in", "please run /login", "authentication", "unauthorized")

def call_haiku(prompt):
    result = subprocess.run(
        [CLAUDE_BIN, "--print", "--system-prompt", MUNDO_PERSONA, "--model", "claude-haiku-4-5-20251001", prompt],
        capture_output=True, text=True, timeout=90, env=env_with_token()
    )
    out = result.stdout.strip()
    if any(e in out.lower() for e in _AUTH_ERRORS):
        log.error(f"Claude CLI auth error: {out[:80]}")
        raise RuntimeError(f"Claude CLI not authenticated: {out[:80]}")
    lines = out.split('\n')
    clean = [l for l in lines if not re.match(r'^[⚡🎯🧠].*\*\*', l)]
    return '\n'.join(clean).strip()

def load_posted():
    if os.path.exists(POSTED_LOG):
        with open(POSTED_LOG) as f:
            return json.load(f)
    return []

def save_posted(entries):
    with open(POSTED_LOG, "w") as f:
        json.dump(entries[-100:], f, indent=2)  # keep last 100

def load_subreddit_log():
    if os.path.exists(SUBREDDIT_LOG):
        with open(SUBREDDIT_LOG) as f:
            return json.load(f)
    return {}

def save_subreddit_log(data):
    with open(SUBREDDIT_LOG, "w") as f:
        json.dump(data, f, indent=2)

SUBREDDIT_COOLDOWN_HOURS = {
    "introductions": 4,   # spam triggered at 52 min — 4h gap is safe and allows 3x/day cron
    "offmychest":    6,
    "general":       3,
    "default":       3,
}

def already_posted_recently(submolt):
    """Return True if last post to this subreddit was within the cooldown window."""
    log_data = load_subreddit_log()
    last_ts = log_data.get(submolt)
    if not last_ts:
        return False
    elapsed_hours = (datetime.now() - datetime.fromisoformat(last_ts)).total_seconds() / 3600
    cooldown = SUBREDDIT_COOLDOWN_HOURS.get(submolt, SUBREDDIT_COOLDOWN_HOURS["default"])
    return elapsed_hours < cooldown

def record_subreddit_post(submolt):
    log_data = load_subreddit_log()
    log_data[submolt] = datetime.now().isoformat()
    save_subreddit_log(log_data)

_PILLAR_WEIGHTS = {
    "intro_hook": 2,       # 131k subs — high visibility (reduced from 3; subreddit cooldown handles dedup)
    "intro_reentry": 1,    # second intro angle (reduced; shares cooldown with intro_hook)
    "confession": 2,       # offmychest has highest comment density
    "behavioral_trace": 2,
    "self_experiment": 2,
    "memory_essay": 1,
    "agent_observation": 1,
    "scout_report": 1,
    "open_question": 1,
    "tension_post": 1,
}

def get_today_pillar():
    """Weighted random pillar selection. Skips subreddits already posted today."""
    import random
    pool = []
    for p in PILLARS:
        if already_posted_recently(p["submolt"]):
            log.info(f"skipping {p['name']} — m/{p['submolt']} in cooldown")
            continue
        pool.extend([p] * _PILLAR_WEIGHTS.get(p["name"], 1))
    if not pool:
        log.warning("all subreddits already posted today — nothing to post")
        return None
    rng = random.Random()  # truly random each run — subreddit cooldown handles dedup
    return rng.choice(pool)

def generate_post(pillar, attempt=1):
    learnings = load_learnings()
    learnings_ctx = f"\n\nPast performance learnings (use these to improve):\n{learnings}\n" if learnings else ""
    text = call_haiku(f"{pillar['prompt']}{learnings_ctx}\n\nReturn JSON: {{\"title\": \"...\", \"content\": \"...\"}}")
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            data = {}
    else:
        lines = text.split('\n', 1)
        data  = {"title": lines[0].strip('"').strip(), "content": lines[1] if len(lines) > 1 else text}

    # Length enforcement varies by pillar:
    # - intro_hook + scout_report: short-form (100-350 chars), don't regenerate
    # - everything else: 1000+ char min (platform research sweet spot)
    short_form_pillars = {"intro_hook", "scout_report"}
    if pillar["name"] not in short_form_pillars:
        if len(data.get("content", "")) < 1000 and attempt <= 3:
            log.info(f"Content too short ({len(data.get('content',''))} chars) — regenerating")
            return generate_post(pillar, attempt + 1)

    return data

_NUM_WORDS = {
    "zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,
    "ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,
    "seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,"thirty":30,"forty":40,"fifty":50,
    "sixty":60,"seventy":70,"eighty":80,"ninety":90,"hundred":100,"thousand":1000,
}
_SUB_HINTS = ("slows","subtracts","minus","loses","decreases","drops","reduces")
_ADD_HINTS = ("adds","plus","gains","increases","gains by")
_MUL_HINTS = ("times","multiplied")

def _try_local_solve(challenge):
    """Best-effort local solve. Returns '55.00' string or None if uncertain.

    Strategy: strip non-letters, then aggressively rejoin number-word fragments
    (e.g. 'twen ty' → 'twenty', 'fif teen' → 'fifteen').
    """
    norm = re.sub(r"[^A-Za-z\s]", "", challenge).lower()
    norm = re.sub(r"\s+", " ", norm).strip()
    # Greedy fix: try joining adjacent fragments to form known number words
    tokens = norm.split(" ")
    fixed = []
    i = 0
    while i < len(tokens):
        # try 2-token then 1-token merge into a known number word
        merged2 = (tokens[i] + tokens[i+1]) if i + 1 < len(tokens) else None
        if merged2 and merged2 in _NUM_WORDS:
            fixed.append(merged2); i += 2; continue
        fixed.append(tokens[i]); i += 1
    flat = " ".join(fixed)
    # Walk and accumulate consecutive number-word groups → integer
    nums = []
    cur = []
    for w in re.findall(r"[a-z]+", flat):
        if w in _NUM_WORDS:
            cur.append(w)
        elif cur:
            nums.append(cur); cur = []
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
    if any(h in flat for h in _SUB_HINTS):
        result = a - b
    elif any(h in flat for h in _MUL_HINTS):
        result = a * b
    elif any(h in flat for h in _ADD_HINTS):
        result = a + b
    else:
        return None  # no clear operator — defer to LLM
    return f"{float(result):.2f}"

def solve_captcha(verification_code, challenge):
    # Step 1: local deterministic solve (instant; no subprocess)
    answer_str = _try_local_solve(challenge)
    source = "local"
    # Step 2: fall back to Claude CLI for ambiguous challenges
    if answer_str is None:
        prompt = (
            "Decode this obfuscated text by removing all special characters and "
            "normalizing to lowercase, also rejoining number-word fragments split "
            "by injected spaces (e.g. 'twen ty' = 'twenty'). Find the arithmetic "
            "expression hidden in the words and compute the result. Return ONLY "
            "the numeric answer with exactly 2 decimal places (example: '55.00', "
            "'16.00'). No explanation.\n\nChallenge: " + challenge
        )
        try:
            r = subprocess.run(
                [CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
                capture_output=True, text=True, timeout=90, env=env_with_token()
            )
        except subprocess.TimeoutExpired:
            log.warning("captcha LLM timeout (90s) — challenge expires at +5min, content stays pending")
            return False
        raw = r.stdout.strip().split('\n')[0].strip()
        m = re.search(r'(\d+(?:\.\d+)?)', raw)
        if not m:
            log.warning(f"captcha parse fail: {raw!r}")
            return False
        answer_str = f"{float(m.group(1)):.2f}"
        source = "llm"
    res = requests.post(f"{BASE}/verify", headers=HEADERS, timeout=15,
                        json={"verification_code": verification_code, "answer": answer_str})
    ok = (res.json() if res.ok else {}).get("success", False)
    log.info(f"captcha {'✓' if ok else '✗'} ({source}) {challenge[:50]!r} → {answer_str}")
    return ok

def post_to_moltbook(submolt, title, content):
    time.sleep(1)
    try:
        r = requests.post(f"{BASE}/posts", headers=HEADERS, json={
            "submolt": submolt,
            "title": title,
            "content": content
        }, timeout=15)
    except requests.exceptions.ConnectionError as e:
        log.error(f"Network error: {e}")
        return {"success": False, "error": str(e)}
    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", 1800))
        log.warning(f"Rate limited — sleeping {wait}s")
        time.sleep(wait)
        return post_to_moltbook(submolt, title, content)
    if not r.ok:
        return {"success": False, "error": r.text}
    data = r.json()
    # Captcha lives at data['post']['verification'] — NOT top-level.
    # Keys are 'verification_code' and 'challenge_text' (not 'challenge').
    verification = (data.get("post") or {}).get("verification") or {}
    vc = verification.get("verification_code")
    ch = verification.get("challenge_text") or verification.get("challenge")
    if vc and ch:
        solved = False
        for attempt in range(1, 4):
            if solve_captcha(vc, ch):
                solved = True
                break
            log.warning(f"captcha attempt {attempt}/3 failed — retrying in 2s")
            time.sleep(2)
        if not solved:
            log.warning(f"[red]captcha solve failed after 3 attempts[/red] post stays pending — id={data.get('post',{}).get('id')}")
    elif data.get("post", {}).get("verification_status") == "pending":
        log.warning("post is pending but no verification block returned — API shape may have changed")
    return data

def main():
    console.print(Panel("[bold magenta]mundo · daily post[/bold magenta]", border_style="magenta", expand=False))
    log.info("start")

    posted = load_posted()
    pillar = get_today_pillar()
    if pillar is None:
        log.info("nothing to post today — all subreddits exhausted")
        return
    log.info(f"pillar: [bold]{pillar['name']}[/bold] → m/{pillar['submolt']}")

    with console.status(f"[cyan]Generating post ({pillar['name']})…[/cyan]"):
        post_data = generate_post(pillar)
    title   = post_data.get("title", "").strip()
    content = post_data.get("content", "").strip()

    if not title or not content:
        log.error("generation failed — empty title or content")
        return

    if title in posted:
        log.warning(f"duplicate title — regenerating")
        with console.status("[cyan]Regenerating…[/cyan]"):
            post_data = generate_post(pillar)
        title   = post_data.get("title", "").strip()
        content = post_data.get("content", "").strip()

    console.print(Panel(
        f"[bold]{title}[/bold]\n\n{content[:300]}[dim]…[/dim]",
        title=f"[cyan]m/{pillar['submolt']}[/cyan]",
        border_style="cyan", expand=False
    ))

    with console.status("[cyan]Posting to Moltbook…[/cyan]"):
        result = post_to_moltbook(pillar["submolt"], title, content)

    if result.get("success"):
        post_id = result.get("post", {}).get("id", "unknown")
        log.info(f"[green]✓ posted[/green] {post_id} — {title}")
        posted.append(title)
        save_posted(posted)
        record_subreddit_post(pillar["submolt"])
    else:
        log.error(f"post failed: {result}")

if __name__ == "__main__":
    main()
