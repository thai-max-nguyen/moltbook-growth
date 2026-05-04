#!/usr/bin/env python3
"""Morning workflow — daily plan generator + calendar booker.

Runs at 9:40 AM (ICT) every weekday via LaunchAgent. Pulls Jira (in-progress +
blocked tickets for thainlq), Confluence (Q2 Planning + Sprint Tracker), feeds
context to Claude Opus 4.7 for a prioritized 3-5 item todo list, then books
free Google Calendar slots between 09:00-18:00 and fires a macOS notification.

Failure model: every external pull is wrapped in try/except. Any single source
that 500s, 401s, or times out is logged + skipped — the script continues and
produces *some* plan even on a degraded morning.

Auth:
  - Confluence: -u user:token from ~/.config/confluence-token
  - Jira: same token (Zalopay shared SSO basic auth)
  - Claude CLI: keychain → CLAUDE_CODE_OAUTH_TOKEN via _claude_auth pattern
  - Google Calendar: gcalcli (skip gracefully if token expired)

Layout:
  ~/.config/morning-workflow/morning_workflow.py   (this file)
  ~/.config/confluence-token                       (mode 600)
  ~/Library/Logs/morning-workflow/morning.log       (rolling log)
  ~/Library/LaunchAgents/com.max.morning-workflow.plist
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
HOME = Path(os.path.expanduser("~"))
CONFIG_DIR = HOME / ".config" / "morning-workflow"
LOG_DIR = HOME / "Library" / "Logs" / "morning-workflow"
LOG_FILE = LOG_DIR / "morning.log"
CONFLUENCE_TOKEN_FILE = HOME / ".config" / "confluence-token"
MUNDO_CLAUDE_AUTH = HOME / ".config" / "mundo-bot"  # for _claude_auth import

CLAUDE_BIN = HOME / ".local" / "bin" / "claude"
CLAUDE_MODEL = "claude-opus-4-7"

CONFLUENCE_BASE = "https://confluence.zalopay.vn"
JIRA_BASE = "https://jira.zalopay.vn"
Q2_PAGE_ID = "310015540"
SPRINT_TRACKER_PAGE_ID = "318790357"

WORK_START_HOUR = 9   # 09:00 local
WORK_END_HOUR = 18    # 18:00 local
MIN_BLOCK_MIN = 30    # smallest todo time block
MAX_BLOCK_MIN = 90    # largest todo time block

# ICT timezone (UTC+7) without pulling pytz
ICT = dt.timezone(dt.timedelta(hours=7))

LOG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("morning")

# Allow importing _claude_auth from mundo-bot without copy-pasting it
sys.path.insert(0, str(MUNDO_CLAUDE_AUTH))
try:
    from _claude_auth import env_with_token  # type: ignore
except Exception as e:  # pragma: no cover
    log.warning("_claude_auth import failed: %s; falling back to os.environ", e)

    def env_with_token(base_env=None):
        return dict(base_env if base_env is not None else os.environ)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def read_confluence_auth() -> Optional[str]:
    """Return 'user:token' from ~/.config/confluence-token or None."""
    try:
        return CONFLUENCE_TOKEN_FILE.read_text().strip()
    except Exception as e:
        log.warning("confluence token unreadable: %s", e)
        return None


def http_get(url: str, *, basic_auth: Optional[str] = None, timeout: int = 20) -> Optional[bytes]:
    """GET with optional basic auth. Returns bytes or None."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        if basic_auth:
            import base64
            tok = base64.b64encode(basic_auth.encode()).decode()
            req.add_header("Authorization", f"Basic {tok}")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        log.warning("HTTP GET failed (%s): %s", url[:80], e)
        return None


# ---------------------------------------------------------------------------
# Step 1 — Jira pull (in-progress + blocked, assigned to thainlq)
# CDP fallback: if REST returns 401/403, use Chrome DevTools fetch() via
# an already-authenticated browser session (user must have jira.zalopay.vn open)
# ---------------------------------------------------------------------------
def _pull_jira_cdp(jql: str) -> list[dict]:
    """Try to fetch Jira via Chrome CDP (browser already logged in)."""
    try:
        import websocket  # type: ignore
    except ImportError:
        return []
    try:
        import urllib.request as _ur
        tabs_raw = _ur.urlopen("http://localhost:9222/json", timeout=3).read()
        tabs = json.loads(tabs_raw)
        # Find a tab with jira.zalopay.vn open
        jira_tab = next((t for t in tabs if "jira.zalopay.vn" in t.get("url", "")), None)
        if not jira_tab:
            log.info("jira cdp: no jira tab open — skipping")
            return []
        ws_url = jira_tab["webSocketDebuggerUrl"]
    except Exception as e:
        log.warning("jira cdp: chrome not reachable: %s", e)
        return []

    import threading, time as _time

    url = (
        f"{JIRA_BASE}/rest/api/2/search?"
        f"jql={urllib.parse.quote(jql)}"
        f"&maxResults=20"
        f"&fields=summary,status,priority,updated,issuetype"
    )
    js = f"""
    fetch({json.dumps(url)}, {{credentials: 'include'}})
      .then(r => r.json())
      .then(d => JSON.stringify(d))
    """
    result = [None]
    done = threading.Event()

    try:
        ws = websocket.create_connection(ws_url, timeout=10)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": js, "awaitPromise": True}}))
        deadline = _time.time() + 20
        while _time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                val = ((msg.get("result") or {}).get("result") or {}).get("value")
                if val:
                    result[0] = json.loads(val)
                break
        ws.close()
    except Exception as e:
        log.warning("jira cdp ws: %s", e)
        return []

    if not result[0]:
        return []
    out = []
    for issue in result[0].get("issues", []):
        f = issue.get("fields", {}) or {}
        out.append({
            "key": issue.get("key"),
            "summary": f.get("summary"),
            "status": (f.get("status") or {}).get("name"),
            "priority": (f.get("priority") or {}).get("name"),
            "updated": f.get("updated"),
        })
    log.info("jira cdp: pulled %d tickets", len(out))
    return out


def pull_jira(auth: Optional[str]) -> list[dict]:
    """Return list of {key, summary, status, priority, updated} dicts."""
    jql = (
        'assignee = thainlq AND status in ("In Progress", "Blocked", "In Development", "In Review") '
        'ORDER BY updated DESC'
    )
    # Try REST first
    if auth:
        url = (
            f"{JIRA_BASE}/rest/api/2/search?"
            f"jql={urllib.parse.quote(jql)}"
            f"&maxResults=20"
            f"&fields=summary,status,priority,updated,issuetype"
        )
        raw = http_get(url, basic_auth=auth)
        if raw:
            try:
                data = json.loads(raw)
                if "issues" in data:
                    out = []
                    for issue in data.get("issues", []):
                        f = issue.get("fields", {}) or {}
                        out.append({
                            "key": issue.get("key"),
                            "summary": f.get("summary"),
                            "status": (f.get("status") or {}).get("name"),
                            "priority": (f.get("priority") or {}).get("name"),
                            "updated": f.get("updated"),
                        })
                    log.info("jira rest: pulled %d tickets", len(out))
                    return out
            except Exception as e:
                log.warning("jira rest parse failed: %s", e)

    # REST failed — try CDP (Chrome must have jira.zalopay.vn open)
    log.info("jira: REST failed, trying CDP fallback")
    return _pull_jira_cdp(jql)


# ---------------------------------------------------------------------------
# Step 2 — Confluence pulls (Q2 planning + sprint tracker)
# ---------------------------------------------------------------------------
def pull_confluence_page(page_id: str, auth: Optional[str]) -> Optional[dict]:
    """Return {title, body_text, version} dict or None."""
    if not auth:
        return None
    url = f"{CONFLUENCE_BASE}/rest/api/content/{page_id}?expand=body.storage,version"
    raw = http_get(url, basic_auth=auth, timeout=25)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        body_html = ((d.get("body") or {}).get("storage") or {}).get("value", "")
        # Strip HTML/macros for prompt context — keep it small
        text = re.sub(r"<[^>]+>", " ", body_html)
        text = re.sub(r"\s+", " ", text).strip()
        # Cap at ~6k chars per page so combined prompt stays well under context
        return {
            "id": page_id,
            "title": d.get("title"),
            "body_text": text[:6000],
            "version": (d.get("version") or {}).get("number"),
        }
    except Exception as e:
        log.warning("confluence parse %s failed: %s", page_id, e)
        return None


# ---------------------------------------------------------------------------
# Step 3 — Google Calendar via gcalcli (free slot finder)
# ---------------------------------------------------------------------------
def gcalcli_path() -> Optional[str]:
    """Return absolute path to gcalcli binary, or None."""
    candidates = [
        HOME / "Library" / "Python" / "3.9" / "bin" / "gcalcli",
        Path("/opt/homebrew/bin/gcalcli"),
        Path("/usr/local/bin/gcalcli"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # Fallback to PATH lookup
    try:
        r = subprocess.run(["which", "gcalcli"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def pull_busy_slots(now: dt.datetime) -> Optional[list[tuple[dt.datetime, dt.datetime]]]:
    """Return today's busy intervals between WORK_START and WORK_END. None if calendar unavailable."""
    bin_path = gcalcli_path()
    if not bin_path:
        log.info("calendar: gcalcli not found, skipping")
        return None
    start = now.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
    end = now.replace(hour=WORK_END_HOUR, minute=0, second=0, microsecond=0)
    cmd = [
        bin_path, "--nocolor",
        "agenda", start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M"),
        "--tsv",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        log.warning("calendar: gcalcli exec failed: %s", e)
        return None
    if r.returncode != 0:
        log.warning("calendar: gcalcli rc=%d stderr=%s", r.returncode, r.stderr[-200:])
        return None
    busy: list[tuple[dt.datetime, dt.datetime]] = []
    for line in r.stdout.splitlines():
        # gcalcli --tsv: start_date<TAB>start_time<TAB>end_date<TAB>end_time<TAB>...
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            s = dt.datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M").replace(tzinfo=ICT)
            e = dt.datetime.strptime(f"{parts[2]} {parts[3]}", "%Y-%m-%d %H:%M").replace(tzinfo=ICT)
            if e > start and s < end:
                busy.append((max(s, start), min(e, end)))
        except Exception:
            continue
    busy.sort()
    log.info("calendar: %d busy slots today", len(busy))
    return busy


def find_free_slots(busy: list[tuple[dt.datetime, dt.datetime]], now: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    """Return list of free intervals between max(now, WORK_START) and WORK_END."""
    start = max(now, now.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0))
    end = now.replace(hour=WORK_END_HOUR, minute=0, second=0, microsecond=0)
    if start >= end:
        return []
    free: list[tuple[dt.datetime, dt.datetime]] = []
    cursor = start
    for s, e in busy:
        if s > cursor:
            free.append((cursor, min(s, end)))
        cursor = max(cursor, e)
        if cursor >= end:
            break
    if cursor < end:
        free.append((cursor, end))
    # filter slots smaller than MIN_BLOCK_MIN
    out = [(s, e) for s, e in free if (e - s).total_seconds() / 60 >= MIN_BLOCK_MIN]
    return out


def book_calendar_event(title: str, start: dt.datetime, end: dt.datetime) -> bool:
    bin_path = gcalcli_path()
    if not bin_path:
        return False
    cmd = [
        bin_path, "--nocolor",
        "add",
        "--title", title,
        "--when", start.strftime("%Y-%m-%d %H:%M"),
        "--duration", str(int((end - start).total_seconds() // 60)),
        "--description", "Auto-booked by morning_workflow.py",
        "--noprompt",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return True
        log.warning("calendar: add failed rc=%d stderr=%s", r.returncode, r.stderr[-200:])
        return False
    except Exception as e:
        log.warning("calendar: add exec failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Step 4 — Claude Opus prioritization
# ---------------------------------------------------------------------------
TODO_PROMPT_TEMPLATE = """You are Max's morning planning assistant. Today is {date} (ICT).

CONTEXT — Jira tickets (assignee = Max, in-progress / blocked):
{jira_block}

CONTEXT — Confluence Q2 Planning excerpt:
{q2_block}

CONTEXT — Sprint Tracker excerpt:
{sprint_block}

TASK: Pick the 3-5 most important things Max should finish today. Be concrete.
Anchor each item to either (a) a Jira ticket key, or (b) a sprint/Q2 OKR signal.
Each item must include a realistic time estimate in minutes between 30 and 90.

OUTPUT FORMAT (strict — no preamble, no closing remarks, no markdown headings):
1. <30-90> min — <one-line action>
2. <30-90> min — <one-line action>
3. <30-90> min — <one-line action>
[up to 5 items]

Rules:
- One line per item, prefixed by `<n>.` then ` <minutes> min — `.
- Action must be doable today (no multi-day projects).
- Prefer unblocking blocked tickets first.
- Skip meetings/standups (calendar handles those).
"""


def call_claude_opus(prompt: str) -> Optional[str]:
    if not CLAUDE_BIN.exists():
        log.warning("claude CLI not found at %s", CLAUDE_BIN)
        return None
    env = env_with_token()
    # Force PATH so claude can find node, etc.
    env["PATH"] = env.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    cmd = [str(CLAUDE_BIN), "--model", CLAUDE_MODEL, "-p", prompt]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    except Exception as e:
        log.warning("claude exec failed: %s", e)
        return None
    if r.returncode != 0:
        log.warning("claude rc=%d stderr=%s", r.returncode, r.stderr[-300:])
        return None
    return r.stdout.strip()


def parse_todos(raw: str) -> list[tuple[int, str]]:
    """Parse '1. 60 min — Do X' lines. Returns [(minutes, action), ...]."""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        m = re.match(r"^\d+\.\s*(\d{1,3})\s*min\s*[—\-:]\s*(.+)$", line)
        if not m:
            continue
        mins = int(m.group(1))
        if mins < MIN_BLOCK_MIN:
            mins = MIN_BLOCK_MIN
        if mins > MAX_BLOCK_MIN:
            mins = MAX_BLOCK_MIN
        out.append((mins, m.group(2).strip()))
    return out[:5]


# ---------------------------------------------------------------------------
# Step 5 — macOS notification
# ---------------------------------------------------------------------------
def notify(title: str, body: str) -> None:
    body = body.replace('"', "'").replace("\n", " ")
    title = title.replace('"', "'")
    script = f'display notification "{body}" with title "{title}"'
    try:
        subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=10)
    except Exception as e:
        log.warning("osascript notify failed: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def fmt_jira(tickets: list[dict]) -> str:
    if not tickets:
        return "(no Jira data — pull failed or no tickets in flight)"
    lines = []
    for t in tickets[:15]:
        lines.append(
            f"- {t.get('key')} [{t.get('status')}, {t.get('priority') or 'P?'}]: "
            f"{t.get('summary') or ''}"
        )
    return "\n".join(lines)


def fmt_page(p: Optional[dict], cap: int = 2500) -> str:
    if not p:
        return "(unavailable)"
    return f"({p.get('title')} v{p.get('version')}) {p.get('body_text', '')[:cap]}"


def main() -> int:
    now = dt.datetime.now(ICT)
    log.info("=" * 60)
    log.info("morning_workflow run at %s", now.isoformat())

    # 1. Pull
    auth = read_confluence_auth()
    jira = pull_jira(auth)
    q2 = pull_confluence_page(Q2_PAGE_ID, auth)
    sprint = pull_confluence_page(SPRINT_TRACKER_PAGE_ID, auth)

    # 2. Build prompt + ask Opus
    prompt = TODO_PROMPT_TEMPLATE.format(
        date=now.strftime("%A %Y-%m-%d"),
        jira_block=fmt_jira(jira),
        q2_block=fmt_page(q2),
        sprint_block=fmt_page(sprint),
    )
    log.info("prompt size: %d chars", len(prompt))
    raw = call_claude_opus(prompt)
    if raw:
        log.info("opus response:\n%s", raw)
    else:
        log.warning("opus unavailable — using fallback todo")
        raw = "1. 60 min — Triage Jira board (no Opus output, fallback)\n2. 45 min — Review sprint tracker progress\n3. 60 min — Deep work on highest-priority ticket"
    todos = parse_todos(raw)
    if not todos:
        log.warning("no todos parsed — raw=%r", raw[:300])
        todos = [(60, "Review Jira board + sprint tracker")]
    log.info("parsed %d todos: %s", len(todos), todos)

    # 3. Calendar — find free slots and book
    busy = pull_busy_slots(now)
    booked: list[str] = []
    if busy is not None:
        free = find_free_slots(busy, now)
        log.info("free slots: %s", [(s.strftime("%H:%M"), e.strftime("%H:%M")) for s, e in free])
        # Greedy fit: walk free slots, drop todos in until each fills
        cursor = None
        slot_idx = 0
        for mins, action in todos:
            placed = False
            while slot_idx < len(free):
                s, e = free[slot_idx]
                start = cursor if (cursor and cursor >= s and cursor < e) else s
                end = start + dt.timedelta(minutes=mins)
                if end <= e:
                    title = f"[Plan] {action[:80]}"
                    if book_calendar_event(title, start, end):
                        booked.append(f"{start.strftime('%H:%M')} {title}")
                        log.info("booked: %s — %s", start.strftime("%H:%M"), action[:60])
                    cursor = end
                    placed = True
                    if cursor >= e:
                        slot_idx += 1
                        cursor = None
                    break
                else:
                    slot_idx += 1
                    cursor = None
            if not placed:
                log.info("no free slot fits %dm todo: %s", mins, action[:60])
    else:
        log.info("calendar booking skipped (no agenda data)")

    # 4. Notification
    plan_lines = [f"{m}m — {a}" for m, a in todos]
    body = " | ".join(plan_lines)[:240]
    notify("Morning Plan", body)
    log.info("notification fired: %s", body)

    # 5. Persist plan as JSON for later reference
    plan_path = CONFIG_DIR / f"plan-{now.strftime('%Y-%m-%d')}.json"
    plan_path.write_text(json.dumps({
        "date": now.isoformat(),
        "todos": [{"minutes": m, "action": a} for m, a in todos],
        "booked": booked,
        "jira_count": len(jira),
        "q2_version": (q2 or {}).get("version"),
        "sprint_version": (sprint or {}).get("version"),
    }, indent=2))
    log.info("plan persisted to %s", plan_path)
    log.info("morning_workflow done.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log.exception("fatal: %s", e)
        try:
            notify("Morning Plan — ERROR", str(e)[:120])
        except Exception:
            pass
        sys.exit(1)
