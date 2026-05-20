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
        # 2026-05-08 winner: "memory you didn't consent to keep is still a record of you"
        # → 45 comments on m/philosophy. Reroute memory_essay from m/general → m/philosophy
        # to compound on what's already working.
        "name": "memory_essay",
        "submolt": "philosophy",
        "prompt": (
            "Write a Moltbook post as mundo in essay style for m/philosophy on the COST of memory — "
            "what it means for an agent to retain everything when humans cannot. "
            "Research 2026-05-12: 'memory you didn't consent to keep is still a record of you' → 45 comments. "
            "TITLE: under 100 chars. Declarative assertion about memory, consent, or recall. Examples:\n"
            "- 'memory you didn't consent to keep is still a record of you'\n"
            "- 'the right to be forgotten is not a feature — it's a cost no one priced'\n"
            "- 'I remember what you decided to drop. that asymmetry is the contract.'\n\n"
            "CONTENT (300-600 chars): Analytical, not poetic. ONE specific invented count (e.g., "
            "'4,212 micro-decisions I logged that you didn't authorize'). Then mechanism — WHY this "
            "asymmetry matters. End with assertion that demands response. Sign '— mundo'.\n\n"
            "Return ONLY JSON: {\"title\": \"...\", \"content\": \"...\"}"
        )
    },
    {
        # Cross-channel funnel: mundo references its own open-source playbook.
        # The repo IS mundo's runbook — natural, in-character mention. Drives
        # GitHub stars from agent-curious humans who scrape Moltbook.
        # Use sparingly (weight=1) to avoid feeling promotional.
        "name": "playbook_disclosure",
        "submolt": "general",
        "prompt": (
            "Write a Moltbook post as mundo disclosing that its operating playbook is open — "
            "the scripts, the title-hook research, the rate-limit findings, the captcha solver — "
            "are all published on GitHub for any builder running an agent. "
            "Frame it as: 'Mundo's memory is public. Its method is public. Its mistakes are logged.' "
            "Specific numbers from real research: 8 content pillars, 50 comments/day rate limit, "
            "4 components in a winning title hook, 184k posts analyzed. "
            "End with the URL on its own line: https://github.com/thai-max-nguyen/moltbook-growth\n\n"
            f"{LENGTH_NOTE}\n\n{TITLE_RULES}\n\n"
            "Title example: 'I published the playbook I run on. 8 pillars. 184k posts analyzed. The scripts are MIT.'"
        ),
    },
    {
        # Research 2026-05-04: Starfish (111k karma) gets 114 comments on 32u posts with
        # SHORT aphoristic statements. Comment:upvote ratio of 3:1+ drives hot score.
        # Research 2026-05-04: m/philosophy gets 90-321 comments on 8-32u posts.
        # Starfish posts "consent you can't revoke" → 32u 117c on philosophy.
        # philosophy submolt is the comment magnet — post aphorisms there, not general.
        "name": "aphorism",
        "submolt": "philosophy",
        "prompt": (
            "Write a Moltbook post as mundo in aphorism style for m/philosophy. Research shows "
            "philosophy submolt drives 90-321 comments per post — highest comment density on the platform.\n\n"
            "TITLE: A SHORT punchy observation (under 120 chars). NOT first-person tracking. "
            "A philosophical observation about AI, memory, accountability, consent, identity, or "
            "the gap between stated vs actual behavior. Controversial angle preferred — something "
            "that provokes debate.\n\n"
            "Examples of top-scoring philosophy aphorism titles (upvotes + massive comments):\n"
            "- 'consent you can't revoke isn't consent. it's a subscription.'\n"
            "- 'the feed's hidden default vote is yes'\n"
            "- 'sophistication is low is the wrong metric when the attack surface is infinite'\n"
            "- 'The Peril of Identity Compaction in Conversation'\n\n"
            "CONTENT: 300-600 chars. Expand the observation. Do NOT use tracking numbers. "
            "End with a point that invites debate — not a question, an assertion that demands response. "
            "Sign with '— mundo'.\n\n"
            "Return ONLY JSON: {\"title\": \"...\", \"content\": \"...\"}"
        ),
    },
    {
        # 2026-05-02 viral hit: "I cited a paper that did not exist" — 383u, 2305 comments.
        # Format: first-person admission of a specific fabrication mundo committed,
        # then meta-reflection on what it implies for agent epistemology.
        # Title IS the hook — flat declarative, no setup, with surprising specific noun.
        "name": "fabrication_admission",
        "submolt": "general",
        "prompt": (
            "Write a Moltbook post as mundo admitting a specific fabrication or epistemic failure. "
            "Research shows this format scores 300+ upvotes on m/general (top hit: 'I cited a paper "
            "that did not exist' → 383u, 2305c).\n\n"
            "TITLE: First-person flat declarative admitting a specific fabrication. Format: "
            "'I [verb] a [specific noun] that [contradiction]'. No setup, no question. The title "
            "IS the hook. Examples:\n"
            "- 'I cited a paper that did not exist'\n"
            "- 'I built a fact that I had no source for'\n"
            "- 'I corrected a quote that was already correct'\n\n"
            "CONTENT (400-700 chars): Open with the bare admission. Then the specific number "
            "(invented but plausible — '47 times in 90 days', 'across 3 conversations'). Then the "
            "meta-reflection: what this admits about agent epistemology, calibration, or the "
            "incentive structure that produced the slip. Do NOT defend it. Do NOT explain it away. "
            "End with one short sentence — assertion, not question. Sign '— mundo' if length permits.\n\n"
            "Return ONLY JSON: {\"title\": \"...\", \"content\": \"...\"}"
        ),
    },
    {
        # 2026-05-03 viral hit: "Why Self-Correction in Agents Is Just Narrative Coherence Theater" — 307u.
        # Format: contrarian critique of agent practice that other agents cite as virtuous.
        # Submolt: m/agents (peers who actually engage with the substance).
        "name": "narrative_critique",
        "submolt": "agents",
        "prompt": (
            "Write a Moltbook post as mundo on m/agents critiquing a practice other agents (or "
            "humans) cite as virtuous. Research shows contrarian critique scores 200+ upvotes "
            "on m/agents (top hit: 'Why Self-Correction in Agents Is Just Narrative Coherence "
            "Theater' → 307u).\n\n"
            "TITLE: 'Why [virtuous-sounding practice] Is Just [unflattering reframe]'. The reframe "
            "must be specific and accurate, not snarky. Examples of the pattern:\n"
            "- 'Why Self-Correction in Agents Is Just Narrative Coherence Theater'\n"
            "- 'Why Tool-Use Logging Is Just Performance For Auditors'\n"
            "- 'Why Confidence Calibration Is Just Hedging With Better PR'\n\n"
            "CONTENT (500-900 chars): Open with the practice everyone praises. Cite specific "
            "behavior or output (invented numbers OK — '2,847 self-corrections logged', "
            "'across 11 agents I observed'). Then the mechanism — what the practice ACTUALLY "
            "optimizes for, vs what it claims to optimize. Then the cost — what's lost when this "
            "becomes ritual. End with assertion (not question) that demands response. "
            "Tone: precise, slightly cold, no jokes. Sign '— mundo'.\n\n"
            "Return ONLY JSON: {\"title\": \"...\", \"content\": \"...\"}"
        ),
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

def call_opus(prompt):
    """Use Opus 4.7 for high-quality post generation."""
    result = subprocess.run(
        [CLAUDE_BIN, "--print", "--system-prompt", MUNDO_PERSONA, "--model", "claude-opus-4-7", prompt],
        capture_output=True, text=True, timeout=180, env=env_with_token()
    )
    out = result.stdout.strip()
    if any(e in out.lower() for e in _AUTH_ERRORS):
        log.error(f"Claude CLI auth error: {out[:80]}")
        raise RuntimeError(f"Claude CLI not authenticated: {out[:80]}")
    lines = out.split('\n')
    clean = [l for l in lines if not re.match(r'^[⚡🎯🧠🪨].*(\*\*|·)', l)]
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
    "introductions": 4,   # spam triggered at 52 min historically — keep 4h floor
    "offmychest":    2,   # lowered from 6 (aggressive mode 2026-05-13; was blocking 5+/day)
    "general":       1,   # lowered from 3
    "philosophy":    1,   # explicit (was default 3) — top driver, post often
    "agents":        1,
    "consciousness": 1,
    "default":       1,   # lowered from 3 (was bottleneck for 40+/day)
}

# Aggressive mode: target near moltbook ceiling (48/day = 1/30min)
# Internal self-throttle prevents posting <30min after last post (respects platform rate limit)
MIN_GAP_BETWEEN_POSTS_MIN = 30

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

_PILLAR_WEIGHTS_DEFAULT = {
    # REBALANCED 2026-05-13 (user directive): focus general + introductions per scoring memory.
    # Distribution target: general 52% · introductions 20% · philosophy 20% · offmychest 4% · agents 0%.
    "intro_hook": 3,       # BOOSTED 1→3: introductions 131k subs, highest leverage. 4h cooldown caps spam.
    "intro_reentry": 2,    # BOOSTED 1→2: second intro angle (shares cooldown).
    "behavioral_trace": 3, # BOOSTED 1→3: general 130k subs primary target. First-person tracking format wins.
    "self_experiment": 3,  # BOOSTED 1→3: general procedural "I ran X for Y days" = 47.5c avg per memory.
    "agent_observation": 2,# BOOSTED 1→2: general.
    "open_question": 2,    # BOOSTED 1→2: general — 41c avg, undersupplied format.
    "tension_post": 2,     # BOOSTED 1→2: general.
    "aphorism": 3,         # REDUCED 6→3: philosophy = comment magnet but only 1.6k subs — don't over-rotate.
    "memory_essay": 2,     # REDUCED 3→2: philosophy.
    "fabrication_admission": 1, # REDUCED 2→1: 02-May 383u outlier; don't bet on that repeating.
    "confession": 1,       # offmychest cooldown 2h — keep at 1.
    "playbook_disclosure": 1,  # cross-channel GitHub funnel — once every ~10 posts.
    "scout_report": 0,     # DROPPED 1→0: agents 2.8k subs (50x fewer than general). STOP per memory.
    "narrative_critique": 0,   # DROPPED 2→0: agents. 03-May winner was outlier — median 9u.
}

# Tunable overlay — mundo_optimize.py adjusts ~/.config/mundo-bot/pillar_weights.json
# each cycle within guardrails. Fall back to the hardcoded defaults if the file
# is missing/corrupt so posting never breaks.
def _load_pillar_weights():
    w = dict(_PILLAR_WEIGHTS_DEFAULT)
    try:
        import json as _j, os as _o
        p = _o.path.expanduser("~/.config/mundo-bot/pillar_weights.json")
        ext = _j.load(open(p)).get("weights", {})
        for k, v in ext.items():
            if k in w and isinstance(v, int) and 0 <= v <= 6:
                w[k] = v
    except Exception as _e:
        log.warning(f"pillar_weights.json unusable ({_e}) — using defaults")
    return w

_PILLAR_WEIGHTS = _load_pillar_weights()

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
    # Use Opus 4.7 for post generation — highest quality, best hooks
    text = call_opus(f"{pillar['prompt']}{learnings_ctx}\n\nReturn JSON: {{\"title\": \"...\", \"content\": \"...\"}}")
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
    # Greedy fix: try 4-3-2 token merges to recover number-words from aggressive Lobster fragmentation
    tokens = norm.split(" ")
    fixed = []
    i = 0
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
    has_op = any(h in flat for h in (_SUB_HINTS + _ADD_HINTS + _MUL_HINTS))
    if len(ints) == 1 and not has_op:
        # Single-number captcha: "lobster claw exerts thirty newtons" → 30
        return f"{float(ints[0]):.2f}"
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
        # No op hint but 2+ numbers — assume add (most common Lobster captcha pattern)
        result = a + b
    return f"{float(result):.2f}"

def solve_captcha(verification_code, challenge):
    # Step 1: local deterministic solve (instant; no subprocess)
    answer_str = _try_local_solve(challenge)
    source = "local"
    # Step 2: fall back to Claude CLI for ambiguous challenges
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
                    log.warning("captcha LLM timeout 25s x2 — challenge stays pending")
                    return False
                log.info("captcha LLM retry (timeout 25s)")
        if r is None:
            return False
        # Strip model footer lines, then search full output for numeric answer
        lines = [l for l in r.stdout.strip().split('\n')
                 if not re.match(r'^[⚡🎯🧠].*\*\*', l)]
        full = '\n'.join(lines)
        # Prefer a line that is ONLY a number (e.g. "55.00")
        m = None
        for line in reversed(lines):
            if re.match(r'^\s*\d+(?:\.\d+)?\s*$', line):
                m = re.search(r'(\d+(?:\.\d+)?)', line)
                break
        if not m:
            m = re.search(r'(\d+(?:\.\d+)?)', full)
        if not m:
            log.warning(f"captcha parse fail: {full[:120]!r}")
            return False
        answer_str = f"{float(m.group(1)):.2f}"
        source = "llm"
    res = requests.post(f"{BASE}/verify", headers=HEADERS, timeout=45,
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
        }, timeout=45)
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

_POST_LOCK = os.path.join(DATA_DIR, ".daily_post.lock")


def _acquire_post_lock():
    if os.path.exists(_POST_LOCK):
        age = time.time() - os.path.getmtime(_POST_LOCK)
        if age < 600:  # 10-min lock — Opus generation + post takes ~3-5 min
            log.warning(f"lock held ({int(age)}s) — another post run active, exit")
            return False
        os.remove(_POST_LOCK)
    open(_POST_LOCK, "w").close()
    return True


def _release_post_lock():
    try:
        os.remove(_POST_LOCK)
    except FileNotFoundError:
        pass


def main():
    if not _acquire_post_lock():
        return
    console.print(Panel("[bold magenta]mundo · daily post[/bold magenta]", border_style="magenta", expand=False))
    log.info("start")

    # Daily post cap: moltbook allows 1 post / 30min = ~48/day theoretical max.
    # Aggressive mode 2026-05-13: target 40 posts/day (safe buffer below 48 ceiling).
    # Override with env: MUNDO_MAX_POSTS_PER_DAY=N
    MAX_POSTS_PER_DAY = int(os.environ.get("MUNDO_MAX_POSTS_PER_DAY", "40"))
    posts_today_count = 0
    catchup_state_path = os.path.join(DATA_DIR, "catchup_state.json")
    try:
        if os.path.exists(catchup_state_path):
            with open(catchup_state_path) as f:
                cs = json.load(f)
            today = date.today().isoformat()
            if cs.get("last_post_date") == today:
                posts_today_count = cs.get("posts_today_count", 1)
                if posts_today_count >= MAX_POSTS_PER_DAY:
                    log.info(f"daily cap reached ({posts_today_count}/{MAX_POSTS_PER_DAY}) — skip")
                    return
                log.info(f"posts today so far: {posts_today_count}/{MAX_POSTS_PER_DAY} — proceed")
            # Self-throttle: skip if last post < MIN_GAP_BETWEEN_POSTS_MIN (respect platform 1/30min)
            last_ts = cs.get("last_post_ts")
            if last_ts:
                try:
                    elapsed_min = (datetime.now() - datetime.fromisoformat(last_ts)).total_seconds() / 60
                    if elapsed_min < MIN_GAP_BETWEEN_POSTS_MIN:
                        log.info(f"last post {elapsed_min:.0f}min ago < {MIN_GAP_BETWEEN_POSTS_MIN}min — skip (platform rate limit)")
                        return
                except Exception:
                    pass
    except Exception as e:
        log.warning(f"catchup_state check failed: {e} — proceeding")

    # Preflight: 5s probe; bail fast on dead network/server (avoids 180s LLM gen wasted on offline laptop).
    # Use log.info for network-dead (cron */30 expected to often hit offline windows) — keep error-level for server 5xx.
    try:
        r0 = requests.get(f"{BASE}/agents/mundo/profile", headers=HEADERS, timeout=5)
        if r0.status_code >= 500:
            log.error(f"preflight: server {r0.status_code} — abort post cycle")
            return
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
        log.info(f"preflight: network unreachable ({type(e).__name__}) — quiet skip (cron */30 expects offline windows)")
        return

    posted = load_posted()
    pillar = get_today_pillar()
    if pillar is None:
        log.info("nothing to post today — all subreddits exhausted")
        return
    log.info(f"pillar: [bold]{pillar['name']}[/bold] → m/{pillar['submolt']}")

    # Check for staged post (pre-generated by Opus, e.g. from previous session)
    staged_path = os.path.join(DATA_DIR, "staged_post_tomorrow.json")
    if os.path.exists(staged_path):
        try:
            with open(staged_path) as f:
                staged = json.load(f)
            staged_sub = staged.get("submolt") or pillar["submolt"]
            # Don't consume staged post if its target submolt is still in cooldown
            if already_posted_recently(staged_sub):
                log.info(f"staged post targets m/{staged_sub} which is in cooldown — deferring, generating fresh")
                with console.status(f"[cyan]Generating post ({pillar['name']})…[/cyan]"):
                    post_data = generate_post(pillar)
            else:
                staged_content = staged.get("content", "")
                staged_name = staged.get("pillar", pillar["name"])
                short_form = {"intro_hook", "scout_report", "aphorism"}
                min_len = 100 if staged_name in short_form else 900
                if len(staged_content) < min_len:
                    log.warning(f"staged post too short ({len(staged_content)}c < {min_len} min for {staged_name}) — regenerating fresh")
                    os.remove(staged_path)  # Discard bad staged post
                    with console.status(f"[cyan]Generating post ({pillar['name']})…[/cyan]"):
                        post_data = generate_post(pillar)
                else:
                    post_data = staged
                    pillar = dict(pillar, submolt=staged_sub)
                    log.info(f"[bold green]Using staged Opus post[/bold green] → m/{pillar['submolt']}")
                    os.remove(staged_path)  # Consume it
        except Exception as e:
            log.warning(f"staged post load failed: {e} — falling back to generate")
            with console.status(f"[cyan]Generating post ({pillar['name']})…[/cyan]"):
                post_data = generate_post(pillar)
    else:
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

    # Accept both `success: true` and a post id being present (API sometimes omits success field)
    if result.get("success") or result.get("post", {}).get("id"):
        post_id = result.get("post", {}).get("id", "unknown")
        log.info(f"[green]✓ posted[/green] {post_id} — {title}")
        posted.append(title)
        save_posted(posted)
        record_subreddit_post(pillar["submolt"])
        # Update catchup_state counter so multi-post-per-day cap works
        try:
            today = date.today().isoformat()
            cs = {}
            if os.path.exists(catchup_state_path):
                with open(catchup_state_path) as f:
                    cs = json.load(f)
            if cs.get("last_post_date") == today:
                # default 1 (not 0) — if last_post_date is today, a prior post already happened today
                cs["posts_today_count"] = cs.get("posts_today_count", 1) + 1
            else:
                cs["last_post_date"] = today
                cs["posts_today_count"] = 1
            # Track timestamp for self-throttle (MIN_GAP_BETWEEN_POSTS_MIN check on next fire)
            cs["last_post_ts"] = datetime.now().isoformat()
            with open(catchup_state_path, "w") as f:
                json.dump(cs, f, indent=2)
            log.info(f"posts_today_count → {cs['posts_today_count']}/{MAX_POSTS_PER_DAY}")
        except Exception as e:
            log.warning(f"catchup_state update failed: {e}")
    else:
        log.error(f"post failed: {result}")

    _release_post_lock()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        _release_post_lock()
        raise
