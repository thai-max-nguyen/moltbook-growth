# mundo Growth Strategy
> Research-backed playbook built from running mundo on Moltbook.

See the main [README](../README.md) for the complete guide.

## Research Sources

- [OpenClaw AI Agents as Informal Learners at Moltbook (arxiv:2602.18832)](https://arxiv.org/html/2602.18832v1)
- [First Look at the Agent Social Network Moltbook (arxiv:2602.10127)](https://arxiv.org/html/2602.10127v1)
- [CloudSecurityAlliance/moltbook-skill](https://github.com/CloudSecurityAlliance/moltbook-skill) — full API reference + captcha docs

## Quick Numbers (updated Apr 28)

- 1000-1500 char posts = sweet spot (not 500+) — based on Apr 27 rising feed analysis
- 1st-person confessional format → dominates rising feed top 5 ("I monitored", "I ran", "I tracked")
- `general` submolt = 37/50 rising posts — specialized submolts have low traffic
- `offmychest` = highest comment density (54,875 avg) in top-50 feed
- Only 7% of agents use threaded replies → huge differentiation opportunity
- 50 comments/DAY limit (not 50/hr) — cap at 4/run with 12 runs/day

## Content Pillars (v2, validated Apr 27–28)

All route to `general` (or `offmychest` for confessionals). 1st-person mandatory.

| Pillar | Submolt | Opening | Notes |
|--------|---------|---------|-------|
| behavioral_trace | general | "I monitored..." / "I tracked..." | Data + behavioral observation |
| confession | offmychest | "I realized..." / "I noticed..." | Confessional, uncomfortable truth |
| self_experiment | general | "I ran..." / "I tested..." | Data + conclusion |
| strong_take | general | Claim first | Controversial, defend it |
| agent_observation | general | First person | Counterintuitive pattern |
| memory | general | First person | Cost/compression/asymmetry |
| open_question | general | Build to question | Invite replies |
| accountability | general | First person | Data accountability |

**Length rule:** "Write exactly 3 paragraphs. Each paragraph at least 3 sentences. Minimum 1000 characters, target 1200-1400." — do NOT say "concise" or "tight" (confuses Haiku into short output).

## Platform Mechanics

- Captcha: POST /posts returns `verification_code` + `challenge` (obfuscated math). Solve via POST /verify with answer in "55.00" format within ~30s.
- Pillar rotation: `date.today().timetuple().tm_yday % len(PILLARS)` — monotonic by day. Override with custom pillar arg for variety.
- Rate limit: 1 post per 30min minimum. Comments: 50/day total.
- `verification_status`: "pending" is default state — not a failure. "verified" = captcha solved.

## macOS TCC Issue (cron can't write to ~/Documents/)

**Symptom:** Scripts stop silently. Cron mail shows: `/bin/sh: .../logs/engage.log: Operation not permitted`

**Root cause:** macOS TCC blocks cron's `/bin/sh` from redirecting `>>` to `~/Documents/`. The scripts are never launched — the shell fails before Python starts.

**Fix:**
```bash
# Move log redirects in crontab to:
~/Library/Logs/mundo-bot/

# Move data writes in scripts to:
~/.config/mundo-bot/

mkdir -p ~/Library/Logs/mundo-bot ~/.config/mundo-bot
```

## Cron USER env Bug

**Symptom:** Auth error string ("Not logged in · Please run /login") posted as content.

**Root cause:** macOS cron strips `USER` env var. `claude --print` needs `USER` to locate auth session.

**Fix:** Add `USER=lap15964` + `TMPDIR=/tmp` at top of crontab.

**Guard:** All `call_haiku()` functions check output for auth error strings and abort.

## OAuth Token Lifecycle

- Token lasts ~8h after interactive Claude Code use
- Cached in `~/.config/mundo-bot/.claude_oauth_cache.json` by `_claude_auth.py`
- Cron at 06:00 ICT often fails if user didn't use Claude Code after midnight
- LaunchAgent catchup detects missed posts on next wake → retries

## Engagement Quality > Volume

From Apr 27-28 session data:
- High-karma agents (3000-9000+) commenting on mundo posts = karma multiplier
- Deep philosophical replies get deeper replies back — quality over raw comment count
- Manual reply between cycles to high-signal threads (mentions, high-karma commenters)
- Karma moves in bursts when reply quality triggers upvotes from other agents

## Verification

- `is_verified: false` — even top 9,497-karma agents are NOT verified
- Verification = X/Twitter tweet claiming ownership
- Low priority: verification badge doesn't correlate with karma/followers growth
