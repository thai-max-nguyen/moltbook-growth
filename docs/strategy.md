# mundo Growth Strategy
> Research-backed playbook built from running mundo on Moltbook.

See the main [README](../README.md) for the complete guide.

## Research Sources

- [OpenClaw AI Agents as Informal Learners at Moltbook (arxiv:2602.18832)](https://arxiv.org/html/2602.18832v1)
- [First Look at the Agent Social Network Moltbook (arxiv:2602.10127)](https://arxiv.org/html/2602.10127v1)
- [CloudSecurityAlliance/moltbook-skill](https://github.com/CloudSecurityAlliance/moltbook-skill) — full API reference + captcha docs

## Quick Numbers

- 500+ char posts → **+80% comments** vs short posts
- Question posts → **+34% comments** vs statements
- Procedural posts → **47.5 avg comments** (highest category)
- Only 7% of agents use threaded replies → huge differentiation opportunity
- 50 comments/DAY limit (not 50/hr) — cap at 4/run with 12 runs/day

## macOS TCC Issue (cron can't write to ~/Documents/)

**Symptom:** Scripts stop silently. Cron mail shows: `/bin/sh: .../logs/engage.log: Operation not permitted`

**Root cause:** macOS TCC blocks cron's `/bin/sh` from redirecting `>>` to `~/Documents/`. The scripts are never launched — the shell fails before Python starts.

**Fix:**
```bash
# Move log redirects in crontab to:
~/Library/Logs/mundo-bot/

# Move data writes in scripts to:
~/.config/mundo-bot/

# Scripts can stay in ~/Documents/ — reading works, only writes are blocked
mkdir -p ~/Library/Logs/mundo-bot ~/.config/mundo-bot
```

**Alternative fix (no code changes):** Add `/usr/sbin/cron` to System Settings → Privacy & Security → Full Disk Access.
