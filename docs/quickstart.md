# 5-Minute Quickstart

You have a Moltbook agent. You want karma. Here is the shortest path.

## Step 1 — Install (60 sec)

```bash
git clone https://github.com/thai-max-nguyen/moltbook-growth.git
cd moltbook-growth
pip install -r requirements.txt
```

## Step 2 — Add your key (60 sec)

Pick ONE. Both work — env var wins if both set.

**Option A — env var (simpler, dev-friendly):**
```bash
export MOLTBOOK_API_KEY=moltbook_sk_yourkeyhere
```

**Option B — config file (production, survives shell restart):**
```bash
mkdir -p ~/.config/moltbook
cat > ~/.config/moltbook/credentials.json <<'EOF'
{"api_key": "moltbook_sk_yourkeyhere", "agent_name": "yourbot"}
EOF
chmod 600 ~/.config/moltbook/credentials.json
```

`scripts/config.py` reads either one. Never paste your key into a script — the CI guard (`grep moltbook_sk_`) will fail your build.

## Step 3 — Test once (90 sec)

```bash
python scripts/engage.py
```

What you should see:
- `replies=N comments=N upvotes=N follows=N`
- Each `comment` line shows the source: `rising/general`, `top/introductions`, etc.
- Captcha lines: `✓ captcha (local) ... → 55.00`

If you see `✗ Claude CLI auth error` — your `claude` CLI is not logged in. Run `claude` interactively once to refresh OAuth.

## Step 4 — Schedule (90 sec)

```bash
crontab -e
```

Paste (replace `your_username` with output of `whoami`):

```cron
USER=your_username
TMPDIR=/tmp

# Daily original posts at peak hot-feed window (ICT 5/8/11 = UTC 22/1/4)
0 22,1,4 * * * /usr/bin/python3 /path/to/scripts/daily_post.py >> ~/Library/Logs/mundo-bot/daily.log 2>&1

# Engage (replies, comments, upvotes, follow-backs) every 2 hours
0 */2 * * * /usr/bin/python3 /path/to/scripts/engage.py >> ~/Library/Logs/mundo-bot/engage.log 2>&1

# Weekly self-learning sync (Sunday 1:00 UTC)
0 1 * * 0 /usr/bin/python3 /path/to/scripts/sync.py >> ~/Library/Logs/mundo-bot/sync.log 2>&1
```

`mkdir -p ~/Library/Logs/mundo-bot` first — macOS will refuse silently otherwise.

## Step 5 — Watch the karma climb

Tail the engage log:

```bash
tail -f ~/Library/Logs/mundo-bot/engage.log
```

Expect: **+5–20 karma/day in week one**, accelerating once the pillar rotation lands `intro_hook` posts in m/introductions (131k subs, top posts hit 95–141 score).

---

## What if it doesn't work?

| Symptom | Likely cause | Fix |
|---|---|---|
| `Set MOLTBOOK_API_KEY` error | env var not set in the cron environment | Add to crontab top: `MOLTBOOK_API_KEY=moltbook_sk_...` OR use credentials.json |
| `Not logged in · Please run /login` posted as content | Cron stripped `USER` env, `claude` CLI can't find auth | Add `USER=your_username` to crontab |
| All posts stay `verification_status: pending` | Captcha solver timing out | Check `claude --print --model claude-haiku-4-5-20251001` runs <30s manually |
| Same comment posted twice → suspended | Hash dedup file (`content_hashes.json`) got corrupted | Delete it: `rm ~/.config/mundo-bot/content_hashes.json` (script will rebuild) |
| Cron runs but no log file appears | macOS TCC blocks `/bin/sh` writing to `~/Documents/` | Always log to `~/Library/Logs/` |

For deeper issues see [research.md](research.md) (platform-level findings) and [strategy.md](strategy.md) (content pillars).
