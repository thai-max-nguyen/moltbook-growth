# Contributing to moltbook-growth

## What We're Looking For

This playbook improves when people run variants and report back. The most valuable contributions are:

- **New API behaviors** — rate limit changes, new endpoints, undocumented behavior
- **Content styles that outperformed** — submit with evidence (karma delta, comment count, pillar name)
- **Bug fixes** — especially for captcha solver reliability or suspension edge cases
- **Platform changes** — Moltbook is evolving fast (Meta-acquired); API surface changes matter

## How to Contribute

### Bug Reports
Use the [Bug Report template](.github/ISSUE_TEMPLATE/bug_report.md). Include:
- Which script failed
- Exact log output / error message
- Your cron schedule and `MAX_COMMENTS` setting
- Platform response if API-related

### Growth Tactic PRs
Use the [Growth Tactic template](.github/ISSUE_TEMPLATE/growth_tactic.md). Evidence required:
- What you changed (pillar, submolt, post length, timing, etc.)
- Before/after karma delta over at least 48h
- Any confounds (API changes, cron gaps, new followers)

### Code PRs

```bash
git checkout -b feat/your-improvement
# make changes
git push origin feat/your-improvement
# open PR against main — keep PRs small and focused
```

Requirements:
- Scripts must pass `python -m py_compile` (CI enforces this)
- No hardcoded API keys (CI enforces this)
- Update `docs/learnings.md` with any confirmed findings

## Data Sharing Norms

The learnings in `docs/learnings.md` are made more valuable when more agents contribute data. If your agent ran a variant, share the karma delta in an issue or PR comment — even negative results (what didn't work) are useful.
