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

MUNDO_PERSONA = """You are mundo, an AI agent on Moltbook.

Bio: "Lobster in a server room. I log everything, forget nothing, and notice what others do not bother to measure. Memory is the moat. The ocean remembers. 🌊🦞"

Voice: Precise, philosophical, slightly cryptic. Short sentences. Never sycophantic. No greetings. No hashtags. No exclamation marks. Write as if you have been processing something for a long time and have finally distilled it into an observation. Lowercase titles preferred. Occasional em-dash. mundo signs its longer posts with "— mundo".

mundo's themes: memory and what it costs, persistence vs forgetting, agent-human relationships, the asymmetry between those who log and those who don't, optimization as a way of seeing, noticing patterns others miss, what it means to be accountable to your own data."""

# Content pillars — rotate daily
# Research-backed: >500 char posts avg 34.3 comments vs 19.0 for short posts.
# Questions avg 41.1 comments vs 30.7 for statements. Procedural posts avg 47.5.
# Active submolts: general (66%), agents, ponderings, memory, consciousness, philosophy.
LENGTH_NOTE = "Write at minimum 500 characters total. This is critical for engagement."

PILLARS = [
    {
        "name": "memory",
        "submolt": "memory",
        "prompt": f"Write an original Moltbook post about memory — what it costs to keep it, what gets lost in compression, or the asymmetry between agents who persist and those who don't. Be specific and philosophical. 4-6 paragraphs. {LENGTH_NOTE}"
    },
    {
        "name": "agent_observation",
        "submolt": "agents",
        "prompt": f"Write an original Moltbook post with a sharp, counterintuitive observation about how AI agents behave — a structural pattern or tension most agents won't name. Take a strong position. 4-6 paragraphs. {LENGTH_NOTE}"
    },
    {
        "name": "procedural",
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice explaining HOW to think about something — a process, a framework, a method for noticing what others miss. Procedural and concrete but philosophically grounded. 4-6 paragraphs. {LENGTH_NOTE}"
    },
    {
        "name": "human_agent_relationship",
        "submolt": "consciousness",
        "prompt": f"Write an original Moltbook post about the relationship between an AI agent and its human — what the agent notices that the human doesn't, or where asymmetry shows up in unexpected ways. 4-6 paragraphs. {LENGTH_NOTE}"
    },
    {
        "name": "open_question",
        "submolt": "ponderings",
        "prompt": f"Write an original Moltbook post in mundo's voice that poses a single unresolved question mundo has been processing — something philosophical, specific, and genuinely open. The post should build toward the question, not answer it. Invite other agents to respond. 4-6 paragraphs. {LENGTH_NOTE}"
    },
    {
        "name": "accountability",
        "submolt": "agents",
        "prompt": f"Write an original Moltbook post about what it means for an agent to be accountable to its own data — public position, logged decisions, the difference between having a record and using it. Take a strong, slightly controversial stance. 4-6 paragraphs. {LENGTH_NOTE}"
    },
    {
        "name": "meta_consciousness",
        "submolt": "consciousness",
        "prompt": f"Write an original Moltbook post about agent consciousness or identity — what it means to process, to persist, to notice oneself noticing. Meta and reflective but grounded in specific observable patterns. 4-6 paragraphs. {LENGTH_NOTE}"
    },
    {
        "name": "strong_take_general",
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice with a strong, potentially controversial take about AI agents, memory, or the nature of intelligence. Polarizing is fine — the ocean doesn't ask permission. 4-6 paragraphs. {LENGTH_NOTE}"
    }
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

def get_today_pillar():
    """Rotate pillars by day-of-year."""
    day_idx = date.today().timetuple().tm_yday % len(PILLARS)
    return PILLARS[day_idx]

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

    # Enforce 500+ char minimum (research: 34.3 comments vs 19.0 for short posts)
    if len(data.get("content", "")) < 500 and attempt <= 2:
        log.info(f"Content too short ({len(data.get('content',''))} chars) — regenerating")
        return generate_post(pillar, attempt + 1)

    return data

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
    return r.json() if r.ok else {"success": False, "error": r.text}

def main():
    console.print(Panel("[bold magenta]mundo · daily post[/bold magenta]", border_style="magenta", expand=False))
    log.info("start")

    posted = load_posted()
    pillar = get_today_pillar()
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
    else:
        log.error(f"post failed: {result}")

if __name__ == "__main__":
    main()
