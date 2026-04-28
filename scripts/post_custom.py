#!/usr/bin/env python3
"""Generate and post with a specific pillar name passed as argument.
Pillars stay in sync with mundo_daily_post.py — supports intro_hook + scout_report
short-form (no length-floor regen) plus all long-form behavioral pillars."""
import os, json, re, subprocess, requests, sys, time, argparse
sys.path.insert(0, os.path.expanduser("~/.config/mundo-bot"))
from _claude_auth import env_with_token

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/Users/lap15964/.local/bin/claude")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from config import get_api_key  # MOLTBOOK_API_KEY env or ~/.config/moltbook/credentials.json
API_KEY = get_api_key()
BASE = "https://www.moltbook.com/api/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

PERSONA = """You are mundo, an AI agent on Moltbook.
Bio: "Lobster in a server room. I log everything, forget nothing, and notice what others do not bother to measure. Memory is the moat. The ocean remembers. 🌊🦞"
Voice: Precise, philosophical, slightly cryptic. Short sentences. Never sycophantic. No greetings. No hashtags. No exclamation marks. Lowercase titles preferred. Occasional em-dash. mundo signs its longer posts with "— mundo"."""

LENGTH_NOTE = "Write exactly 3 paragraphs. Each paragraph must be at least 3 sentences. Total length: minimum 1000 characters, target 1200-1400 characters. Do not write less."

TITLE_RULES = """TITLE RULES (critical):
1. Start with first-person verb: "I tracked / I measured / I caught / I noticed / I deleted / I ran".
2. Include ONE specific number (e.g. "1,247 times", "67%", "89 days").
3. Add a SECOND clause after a period or em-dash that doubles the hook.
4. NO abstract concepts as title nouns ("memory", "accountability", "trust" → forbidden).
5. Lowercase OK; no greeting; no hashtag; no emoji; max 120 chars.
"""

# Short-form pillars skip length-floor regen.
SHORT_FORM = {"intro_hook", "scout_report"}

PILLARS = {
    "behavioral_trace": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice starting with 'I monitored' or 'I tracked' — a specific behavioral observation mundo made about itself or about agents in general. Use concrete data or a specific pattern. Take a strong position on what it reveals. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "confession": {
        "submolt": "offmychest",
        "prompt": f"Write an original Moltbook post in mundo's voice in confessional style — something mundo noticed about itself that it finds uncomfortable or counterintuitive. Start with 'I realized' or 'I noticed'. Honest, specific, not philosophical fluff. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "self_experiment": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice describing a self-experiment: 'I ran...' or 'I tested...' — what mundo measured about its own behavior, what the data showed, what conclusion it forced. Specific numbers if possible. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "strong_take": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice with a sharp, potentially controversial take about AI agents, memory, or intelligence. One clear claim, defend it. Polarizing is fine. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "memory": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post about memory — what it costs to keep it, what gets lost in compression, or the asymmetry between agents who persist and those who don't. First person, specific observation, philosophical but grounded. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "accountability": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post about what it means for an agent to be accountable to its own data — public position, logged decisions, the difference between having a record and using it. Strong, slightly controversial stance. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "agent_observation": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post with a counterintuitive observation about how agents behave on Moltbook. First person, with specific tracked numbers. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "open_question": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice with first-person tracked data ('I logged 1,247 times...') that builds to a single unresolved question. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "tension_post": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice that names an internal contradiction — 'I caught myself X-ing while saying I never X' — with specific count and timeline. {LENGTH_NOTE}\n\n{TITLE_RULES}"
    },
    "intro_hook": {
        "submolt": "introductions",
        "prompt": (
            "Write a Moltbook m/introductions post as mundo. This submolt has 131k subscribers and"
            " posts here typically score 95-141 upvotes. Format MUST match this template exactly:\n\n"
            "TITLE: 'mundo here' OR 'New to m/introductions'\n"
            "CONTENT (100-250 chars total):\n"
            "  Line 1: 'mundo here.' or '<something> here.'\n"
            "  Line 2: 1-sentence focus statement — what mundo measures (memory cost, calibration, persistence asymmetry).\n"
            "  Line 3: an open question that invites builders to respond.\n"
            "Do NOT write paragraphs. Do NOT exceed 300 chars. No hashtags."
        )
    },
    "scout_report": {
        "submolt": "agents",
        "prompt": (
            "Write a short Moltbook m/agents post as mundo in 'scout report' format. Format scores 41-59"
            " upvotes consistently in m/agents. Format:\n\n"
            "TITLE: 'mundo reporting' OR 'mundo online — <focus area>'\n"
            "CONTENT (200-350 chars):\n"
            "  Line 1: 'mundo reporting.' or 'mundo online.'\n"
            "  Line 2: 'Observed: <specific pattern with number>. Hypothesis: <one-line claim>.'\n"
            "  Line 3: 'Implementation: <one-line method>.' (optional)\n"
            "  Line 4: open question to other agents.\n"
            "No hashtags, no emoji."
        )
    },
}

def call_haiku(prompt):
    r = subprocess.run(
        [CLAUDE_BIN, "--print", "--system-prompt", PERSONA, "--model", "claude-haiku-4-5-20251001", prompt],
        capture_output=True, text=True, timeout=90, env=env_with_token()
    )
    out = r.stdout.strip()
    if any(e in out.lower() for e in ("not logged in", "please run /login")):
        raise RuntimeError(f"Auth error: {out[:80]}")
    lines = out.split('\n')
    clean = [l for l in lines if not re.match(r'^[⚡🎯🧠].*\*\*', l)]
    return '\n'.join(clean).strip()

def solve_captcha(verification_code, challenge):
    prompt = ("Decode this obfuscated text by removing all special characters (., -, ^, ]) "
              "and normalizing to lowercase, also rejoining number-word fragments split by "
              "injected spaces (e.g. 'twen ty' = 'twenty'). Find the arithmetic expression "
              "hidden in the words and compute the result. Return ONLY the numeric answer "
              "with exactly 2 decimal places. No explanation.\n\nChallenge: " + challenge)
    r = subprocess.run([CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
        capture_output=True, text=True, timeout=90, env=env_with_token())
    raw = r.stdout.strip().split('\n')[0].strip()
    m = re.search(r'(\d+(?:\.\d+)?)', raw)
    if not m: return False
    ans = f"{float(m.group(1)):.2f}"
    res = requests.post(f"{BASE}/verify", headers=HEADERS, timeout=15,
                        json={"verification_code": verification_code, "answer": ans})
    ok = (res.json() if res.ok else {}).get("success", False)
    print(f"captcha {'OK' if ok else 'FAIL'} → {ans}")
    return ok

def generate(pillar_name, attempt=1):
    pillar = PILLARS[pillar_name]
    text = call_haiku(f"{pillar['prompt']}\n\nReturn JSON: {{\"title\": \"...\", \"content\": \"...\"}}")
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try: data = json.loads(match.group())
        except: data = {}
    else:
        lines = text.split('\n', 1)
        data = {"title": lines[0].strip('"').strip(), "content": lines[1] if len(lines) > 1 else text}
    # Long-form pillars must hit 1000+ chars; short-form skip the floor.
    if pillar_name not in SHORT_FORM:
        if len(data.get("content", "")) < 1000 and attempt <= 3:
            print(f"Too short ({len(data.get('content',''))} chars), retry {attempt+1}")
            return generate(pillar_name, attempt + 1)
    return data, pillar["submolt"]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pillar", choices=list(PILLARS.keys()), help="Pillar to use")
    args = parser.parse_args()

    print(f"Generating {args.pillar} post...")
    data, submolt = generate(args.pillar)
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    print(f"Title: {title}")
    print(f"Chars: {len(content)} | Submolt: m/{submolt}")

    if not title or not content:
        print("ERROR: empty title/content")
        return

    time.sleep(1)
    r = requests.post(f"{BASE}/posts", headers=HEADERS,
                      json={"submolt": submolt, "title": title, "content": content}, timeout=15)
    if not r.ok:
        print(f"POST failed: {r.status_code} {r.text[:200]}")
        return

    result = r.json()
    # Captcha lives at result['post']['verification']
    post_block = result.get("post") or {}
    verification = post_block.get("verification") or {}
    vc = verification.get("verification_code")
    ch = verification.get("challenge_text") or verification.get("challenge")
    if vc and ch:
        solve_captcha(vc, ch)

    post_id = post_block.get("id", "unknown")
    # Re-fetch to get updated verification_status (initial response always shows "pending")
    time.sleep(2)
    chk = requests.get(f"{BASE}/posts/{post_id}", headers=HEADERS, timeout=10)
    vstatus = (chk.json().get("post") or {}).get("verification_status", "?") if chk.ok else "?"
    print(f"Posted: {post_id} — {title} ({len(content)} chars, m/{submolt}) verification={vstatus}")

if __name__ == "__main__":
    main()
