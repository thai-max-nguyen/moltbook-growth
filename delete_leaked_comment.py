#!/usr/bin/env python3
"""One-shot: delete the leaked AI-scaffolding reddit comment once the token works.

Context (2026-06-02): reddit_post.py posted the model's own meta-request
("Need the blog post content ... and I'll write the comment") as a real comment
on the r/Python CVE-2026-48710 thread. The generate→publish bug is fixed
(mundo-bot 2c25a3a, see vault feedback_reddit_ai_meta_leak.md), but the leaked
comment is still live and the token was DEAD at fix time.

This script is scheduled every 15 min. It NO-OPS while the token is dead, and the
moment the token recovers it finds the bot's comment(s) matching the leak
signature, deletes them via /api/del, writes a sentinel, and removes its OWN
cron line so it never runs again. Idempotent + self-cleaning.
"""
import os, sys, json, subprocess
from pathlib import Path

import requests

HOME      = Path(os.path.expanduser("~"))
CFG_DIR   = HOME / ".config/mundo-bot"
CONFIG_F  = CFG_DIR / "reddit_config.json"
SENTINEL  = CFG_DIR / ".leaked_comment_deleted"
USERNAME  = "Initial-Process-2875"

# Specific to the leak — won't match a genuine comment.
LEAK_SIGNATURES = (
    "need the blog post content",
    "and i'll write the comment",
    "i'll write the comment",
    "the whole point is to reference",
    "can't write authentic engagement without",
    "drop the key claim",
)


def log(msg):
    ts = subprocess.run(["date", "+%Y-%m-%d %H:%M"], capture_output=True, text=True).stdout.strip()
    print(f"[{ts}] {msg}", flush=True)


def headers(cfg):
    return {
        "Authorization": f"Bearer {cfg['token_v2']}",
        "User-Agent": cfg.get("user_agent", f"reddit-growth-bot/1.0 by {USERNAME}"),
    }


def token_alive(cfg):
    try:
        r = requests.get("https://oauth.reddit.com/api/v1/me", headers=headers(cfg), timeout=12)
        return r.status_code == 200
    except Exception:
        return False


def self_remove_cron():
    """Drop this script's own line from the user crontab."""
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if cur.returncode != 0:
            return
        kept = [ln for ln in cur.stdout.splitlines() if "delete_leaked_comment.py" not in ln]
        subprocess.run(["crontab", "-"], input="\n".join(kept) + "\n", text=True)
        log("self-removed cron line")
    except Exception as e:
        log(f"cron self-remove failed (harmless, remove manually): {e}")


def main():
    if SENTINEL.exists():
        self_remove_cron()  # belt-and-suspenders: ensure cron gone even if a prior run missed it
        return 0
    if not CONFIG_F.exists():
        log("no reddit_config.json — abort")
        return 0
    cfg = json.loads(CONFIG_F.read_text())
    if not cfg.get("token_v2") or not token_alive(cfg):
        # token still dead — quiet no-op, try again next tick
        return 0

    log("token ALIVE — scanning for leaked comment")
    try:
        r = requests.get(
            f"https://oauth.reddit.com/user/{USERNAME}/comments",
            headers=headers(cfg), params={"limit": 100}, timeout=15,
        )
        children = (r.json().get("data") or {}).get("children", []) if r.ok else []
    except Exception as e:
        log(f"fetch comments failed: {e}")
        return 0

    targets = []
    for ch in children:
        d = ch.get("data", {})
        body = (d.get("body") or "").lower()
        if any(sig in body for sig in LEAK_SIGNATURES):
            targets.append((d.get("name"), d.get("body", "")[:80]))

    if not targets:
        # token alive but comment not found — maybe already gone. Mark done.
        log("no leaked comment found (already deleted or aged out) — marking done")
        SENTINEL.write_text(json.dumps({"deleted": [], "note": "not found when token recovered"}))
        self_remove_cron()
        return 0

    deleted = []
    for fullname, preview in targets:
        try:
            resp = requests.post(
                "https://oauth.reddit.com/api/del",
                headers=headers(cfg), data={"id": fullname}, timeout=15,
            )
            if resp.ok:
                log(f"DELETED {fullname}: {preview!r}")
                deleted.append(fullname)
            else:
                log(f"delete {fullname} HTTP {resp.status_code} — will retry next tick")
        except Exception as e:
            log(f"delete {fullname} error: {e} — retry next tick")

    if deleted and len(deleted) == len(targets):
        SENTINEL.write_text(json.dumps({"deleted": deleted}))
        log(f"all {len(deleted)} leaked comment(s) deleted — done")
        self_remove_cron()
    return 0


if __name__ == "__main__":
    sys.exit(main())
