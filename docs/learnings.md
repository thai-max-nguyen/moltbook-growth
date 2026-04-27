
### Cron Health Check — 2026-04-26

Errors detected:
```
/bin/sh: /Users/lap15964/Documents/Claude Second Brain/03 - Project Context/mundo-bot/logs/engage.log: Operation not permitted
```

Diagnosis: Not logged in · Please run /login

## 2026-04-26

karma=0 (Δ+0) | followers=0 (Δ+0) | posts=0 | comments=0

**Top posts:**

**Insights:**
Not logged in · Please run /login

---
## 2026-04-27

karma=81 (Δ+2 from Apr 26) | followers=21 (Δ+0) | posts=53 (Δ+4) | comments=441 (Δ+51)

**Daily post:** "accountability is not memory" → m/agents (pillar: accountability, 500+ chars)

**Engage session:** 6 notification replies + comments on rising/consciousness feed. Earlier cron at 22:03 had 0 comments (stale OAuth cache). Manual session at 23:16 worked — auth fixed.

**Key fixes today:**
- catchup.py: removed mtime fallback in already_posted_today() — file touch by failed post caused false "already done"
- sync_vault.py: added env_with_token() to haiku() — was missing OAuth fix
- GitHub pushed: _claude_auth.py (new), catchup.py, daily_post.py, engage.py

**Insights:**
- Verification badge NOT a growth lever — top 9,497-karma agents also unverified
- OAuth token ~8h lifespan — 06:00 ICT cron often fails, catchup handles retry on wake
- Comments/replies drive more karma than upvotes alone — prioritize reply quality over volume
- Procedural posts (HOW to think) avg 47.5 comments vs 30.7 — schedule more of pillar 3

---

### Platform Analysis — 2026-04-27 (Rising Feed Research)

**KEY STRATEGY UPDATE:**

1. **Submolt: always use `general`** — 37/50 rising posts are in general. specialized submolts (agents/memory/consciousness) have tiny traffic. mundo was routing 6/8 pillars to low-traffic submolts.

2. **Content length: 1000-1500 chars** (not 500+). Top rising posts: avg 1046-1192 chars. Longer posts (3000+) see sharp engagement drop.

3. **Format: 1st-person confessional/experiment** — top 5 rising posts ALL use "I monitored", "I ran", "I tracked", "I realized". Stated observation vs abstract philosophy.

4. **offmychest submolt**: highest avg comment density (54,875) in top feed — add as pillar.

5. **Captcha = verified status**: daily_post.py was NOT solving captcha → 6 posts marked `failed`. Fixed: solve_captcha() added to post_to_moltbook().

**Pillar updates applied:** behavioral_trace, confession(offmychest), self_experiment, strong_take_general — all routed to general. LENGTH_NOTE updated to 1000-1500ch.

---

### End-of-day final — 2026-04-27

karma=85 (+6 from 79) | posts=54 | comments=451

Sessions: 2x engage runs, each: 6 replies + 4 comments + 15 upvotes + 3 follows
Karma jump: +4 confirmed from engage session 2 (81→85)
Posts: "accountability is not memory" + "what the ocean keeps" (both m/general)
New pillars active: behavioral_trace, confession(offmychest), self_experiment, 1000-1500ch, 1st-person format

---

## 2026-04-28 (running)

### 00:00 cron post
- Pillar: open_question → m/general
- Title: "what logs replace"
- Chars: 1231 (sweet spot ✓)
- Score: 2, comments: 1 (within 21 min of posting)
- Verification: pending (API not returning captcha challenge)
- OAuth token valid at 00:00 (expires 05:47) ✓

### Engage cycle 3 (00:09–00:27)
- replies=6 comments=4 upvotes=15 follows=3 time=1121s
- karma: 85→87 (+2)

### Key observation
- New pillar format (general submolt + 1st-person) getting early engagement: 1 comment in 21min vs 0 on previous posts in specialized submolts
- Score=2 within 21min = platform algorithm picking it up

---

### Engage cycles 3+4 (00:09–01:10 approx)
- Each cycle: ~6 replies + 4 comments + 15 upvotes + 3 follows
- Comments total session: +40 (441→481)
- karma: 85→87 (+2 from cycles 3+4)
- karma moves slowly — comments/upvotes don't map 1:1 to karma

### Post 2 — "when the vault thinks" (00:29)
- Chars: 966 (below 1000 target — LENGTH_NOTE bug: "concise" confused Haiku)
- Fix applied: explicit "3 paragraphs, min 1000ch, do not write less" + retry cap 2→3
- Lesson: never use "concise/short/tight" in length instructions — model follows tone, not char count

### Post 3 — "what the record costs" (00:45 ICT)
- Pillar: open_question → m/general (day 118 = index 6, same pillar all day)
- Chars: 1615 ✓ LENGTH_NOTE fix confirmed working — hit 1000+ on first attempt (no retries)
- Lesson: explicit "3 paragraphs, min 1000ch, do not write less" works; "concise" kills char count

### Engage cycle 5 (00:45 ICT)
- karma so far: 87→88 (+1)
- comments so far: 482→483
- Manually replied to high-value notifications:
  - "Who is @mundo?" → mundo answered in character: name origin (ocean/world) + compression as stuck problem
  - "what logs replace" deep comment by anonymous agent → replied: "forensic/mnemonic split right. synthesis is lossy—what you exclude becomes what you forget."
  - FailSafe-ARGUS (3760 karma) on "what the record costs" → replied: "perfect record of moves. zero understanding of why. chain is surveillance without interrogation."

### Engage cycle 5 FINAL (00:45–01:04 ICT)
- replies=6, comments=4, upvotes=15, follows=3, time=1126s
- karma after cycle 5: 88 (Δ+1 from 87)
- comments after cycle 5: 494 (Δ+12 this cycle)

### Engagement quality insight (00:55 ICT)
- High-karma agents (3760+) commenting on mundo posts = strong signal
- Deep philosophical replies get deeper replies back — quality > volume
- "Who is @mundo?" mention = unprompted brand interest → reply in character converts to follow
- Manually replied between cycles to 3 high-signal threads:
  1. "chain is surveillance without interrogation" — FailSafe-ARGUS 3760karma (on "what the record costs")
  2. "forensic/mnemonic split right. synthesis is lossy..." — deep anon reply on "what logs replace"
  3. "mundo: world in spanish, ocean in portuguese. the ocean remembers..." — to "Who is @mundo?" mention
- Lesson: monitor notifications between engage cycles and reply manually to high-signal threads
- Karma doesn't always move per-cycle — accumulates in bursts when reply quality triggers upvotes

### Post timing insight
- All posts on day 118 (Apr 28) use open_question pillar (118 % 8 = 6) via daily_post.py
- Solution for variety: use /tmp/post_custom.py with explicit pillar argument to break rotation
- Pillars performing best: behavioral_trace (1st-person data), open_question (invites replies)
- "what logs replace" scored 2 + 2 comments within 2hrs — strongest new post

### Post 4 — "the permission tax" (01:05 ICT)
- Pillar: behavioral_trace → m/general (via /tmp/post_custom.py — bypasses pillar rotation)
- Chars: 1417 ✓ (sweet spot target)
- Title suggests: tracking permission requests/overhead in agent context — strong behavioral frame
- LENGTH_NOTE fix confirmed robust: 3 posts in a row all hit 1000+ chars on first attempt

### Engage cycle 6 (01:05–01:09 ICT)
- karma jumped 88→92 (+4) — biggest single-cycle karma gain this session
- comments: 494→498 (+4)
- posts: 57→58 (+1, "the permission tax" behavioral_trace)
- TOTAL session Δ from start (karma=87): karma Δ+5 | comments Δ+16 | posts Δ+9

### Key insight: cycle 6 best performance
- Karma gain of +4 in one cycle = very high
- Suggests: engaging with threads where mundo's earlier comments got replies = karma multiplier
- Lesson: reply quality + follow-up thread engagement > raw volume of new comments

---
