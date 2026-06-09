#!/usr/bin/env python3
"""Ban-safety + relevance tests for the Reddit repo-promo pillar.

Guarantees the occasional GitHub-repo promotion can NEVER:
  - post to a banned / non-self-promo-tolerant subreddit
  - exceed a safe frequency (72h cooldown -> <=3/week worst case)
  - beg for stars, or
  - leak self-promo / links into COMMENTS (relevance: promo lives in
    relevant builder posts only; comments stay value-only, link-free).
"""
import sys, os, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)  # mute reddit_post's rich logging
import reddit_post as rp
import time as _t

FAILS = []
def check(name, cond):
    print(("  PASS " if cond else "  FAIL ") + name)
    if not cond:
        FAILS.append(name)

PROFILE = "u_Initial-Process-2875"
TOL = rp.TOLERANT_SUBS
p = rp.PROMO_PILLAR
orig_rand = rp.random.random

check("promo is_promo flag", p.get("is_promo") is True)
check("promo github=moltbook-growth", p.get("github") == "moltbook-growth")
check("promo subs are relevant+tolerant or profile only",
      all(s in TOL or s.startswith("u_") for s in p["subreddits"]))
check("promo subs contain no banned sub",
      not (set(s.lower() for s in p["subreddits"]) & {b.lower() for b in rp.BANNED_SUBS}))

# promo subreddit pick can never leak to a non-tolerant / irrelevant / banned sub
rp.random.seed(1); leaked = False
for _ in range(300):
    sub = rp.pick_subreddit({**p, "subreddits": list(p["subreddits"]) + ["triathlon", "Python", "gardening"]}, {}, 1039)
    if not (sub in TOL or sub == PROFILE):
        leaked = True
check("promo never targets non-tolerant/irrelevant sub (300 draws)", not leaked)
check("banned r/Python filtered from promo",
      rp.pick_subreddit({**p, "subreddits": ["Python", "SideProject", PROFILE]}, {}, 1039) != "Python")

# cooldown
rp.random.random = lambda: 0.01
check("promo eligible when cooldown elapsed",
      any(rp.pick_pillar({}, 1039).get("is_promo") for _ in range(5)))
check("promo BLOCKED within 72h cooldown",
      not rp.pick_pillar({"last_promo_ts": _t.time() - 3600}, 1039).get("is_promo"))
check("promo not forced at very low karma",
      rp.pick_pillar({}, 10).get("is_promo") in (None, False))
rp.random.random = orig_rand

# footer clean
f = rp.append_github_footer("body", "moltbook-growth")
check("footer has repo url", "github.com/thai-max-nguyen/moltbook-growth" in f)
check("footer does not beg for stars",
      not any(w in f.lower() for w in ("please star", "star us", "star the repo", "upvote")))

# comments: link-free + no self-promo (relevance guarantee)
cg = rp.COMMENT_GUIDE.lower()
check("comment guide forbids links", "never include a link" in cg or "no urls" in cg)
check("comment guide forbids self-promo in comments",
      "promote your own" in cg and "instant ban" in cg)

# per-sub 7d cooldown -> profile fallback
from datetime import datetime
now = datetime.now().isoformat()
rp.random.seed(3)
subs = {rp.pick_subreddit(p, {"last_post_SideProject": now, "last_post_IndieDev": now}, 1039) for _ in range(50)}
check("promo -> profile when tolerant subs on 7d cooldown", subs == {PROFILE})

# worst-case weekly cap (random always fires) -> <=3/week via 72h cooldown
rp.random.random = lambda: 0.01
st = {}; base = _t.time(); promos = 0; real = rp.time.time
for slot in range(21):  # 3 posts/day * 7 days
    rp.time.time = (lambda b=base, s=slot: b + s * (24 * 3600 / 3))
    if rp.pick_pillar(st, 1039).get("is_promo"):
        promos += 1; st["last_promo_ts"] = rp.time.time()
rp.time.time = real; rp.random.random = orig_rand
check(f"promo capped <=3/week worst-case (got {promos})", promos <= 3)

print("\nRESULT:", "ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
