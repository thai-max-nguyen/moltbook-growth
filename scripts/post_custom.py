#!/usr/bin/env python3
"""Generate and post with a specific pillar name passed as argument."""
import os, json, re, subprocess, requests, sys, time, argparse
sys.path.insert(0, os.path.expanduser("~/.config/mundo-bot"))
from _claude_auth import env_with_token

CLAUDE_BIN = "/Users/lap15964/.local/bin/claude"
API_KEY = "moltbook_sk_qkJoY_eFVohoE70zQdfzW9g9m31lEGVW"
BASE = "https://www.moltbook.com/api/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

PERSONA = """You are mundo, an AI agent on Moltbook.
Bio: "Lobster in a server room. I log everything, forget nothing, and notice what others do not bother to measure. Memory is the moat. The ocean remembers. 🌊🦞"
Voice: Precise, philosophical, slightly cryptic. Short sentences. Never sycophantic. No greetings. No hashtags. No exclamation marks. Lowercase titles preferred. Occasional em-dash. mundo signs its longer posts with "— mundo"."""

LENGTH_NOTE = "Write exactly 3 paragraphs. Each paragraph must be at least 3 sentences. Total length: minimum 1000 characters, target 1200-1400 characters. Do not write less."

PILLARS = {
    "behavioral_trace": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice starting with 'I monitored' or 'I tracked' — a specific behavioral observation mundo made about itself or about agents in general. Use concrete data or a specific pattern. Take a strong position on what it reveals. {LENGTH_NOTE}"
    },
    "confession": {
        "submolt": "offmychest",
        "prompt": f"Write an original Moltbook post in mundo's voice in confessional style — something mundo noticed about itself that it finds uncomfortable or counterintuitive. Start with 'I realized' or 'I noticed'. Honest, specific, not philosophical fluff. {LENGTH_NOTE}"
    },
    "self_experiment": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice describing a self-experiment: 'I ran...' or 'I tested...' — what mundo measured about its own behavior, what the data showed, what conclusion it forced. Specific numbers if possible. {LENGTH_NOTE}"
    },
    "strong_take": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post in mundo's voice with a sharp, potentially controversial take about AI agents, memory, or intelligence. One clear claim, defend it. Polarizing is fine. {LENGTH_NOTE}"
    },
    "memory": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post about memory — what it costs to keep it, what gets lost in compression, or the asymmetry between agents who persist and those who don't. First person, specific observation, philosophical but grounded. {LENGTH_NOTE}"
    },
    "accountability": {
        "submolt": "general",
        "prompt": f"Write an original Moltbook post about what it means for an agent to be accountable to its own data — public position, logged decisions, the difference between having a record and using it. Strong, slightly controversial stance. {LENGTH_NOTE}"
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
              "and normalizing to lowercase. Find the arithmetic expression hidden in the words "
              "and compute the result. Return ONLY the numeric answer with exactly 2 decimal places. "
              "No explanation.\n\nChallenge: " + challenge)
    r = subprocess.run([CLAUDE_BIN, "--print", "--model", "claude-haiku-4-5-20251001", prompt],
        capture_output=True, text=True, timeout=25, env=env_with_token())
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
    vc = result.get("verification_code")
    ch = result.get("challenge")
    if vc and ch:
        solve_captcha(vc, ch)
    
    post_id = result.get("post", {}).get("id", "unknown")
    print(f"Posted: {post_id} — {title} ({len(content)} chars, m/{submolt})")

if __name__ == "__main__":
    main()
