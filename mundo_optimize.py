#!/usr/bin/env python3
"""mundo_optimize.py — per-cycle, guarded self-tuner for pillar weights.

Runs as a step in growth_cycle.sh AFTER stats. Goal: continuously nudge
content-pillar weights toward what is actually earning engagement, while
being IMPOSSIBLE to misfire — especially while the moltbook API is down.

Signal:  per-submolt EWMA of post score, score = upvotes + 2*comments
         (comments are the proven hot_score driver — see scoring memory).
Action:  at most one pillar +1 (best submolt) and one pillar -1 (worst
         submolt) per run.

Guardrails (why this is safe to run every cycle, unattended):
  - API down / <MIN_SAMPLE posts in window → NO-OP, logged. (handles the
    current moltbook 500 outage cleanly — never thrashes on absent data.)
  - Bounded: any weight moves by at most ±1 per run.
  - Floor 1, ceiling 6 for active pillars (6 = historical max, memory).
  - FROZEN_ZERO pillars stay 0 forever (m/agents killed deliberately —
    optimizer must respect prior strategy, never resurrect).
  - EWMA(alpha=0.3) smoothing → reacts to trends, not single-post noise.
  - Every change appended to mundo_learnings.md + pillar_weights.json
    backed up to .bak before write. Fully reversible.
"""
import json, os, sys, time, urllib.request, urllib.error, datetime

CFG_DIR = os.path.expanduser("~/.config/mundo-bot")
WEIGHTS_F = os.path.join(CFG_DIR, "pillar_weights.json")
STATE_F = os.path.join(CFG_DIR, "mundo_optimize_state.json")
LEARN_F = os.path.join(CFG_DIR, "mundo_learnings.md")
CRED_F = os.path.expanduser("~/.config/moltbook/credentials.json")

ALPHA = 0.3
MIN_SAMPLE = 3          # min posts for a submolt before it can move weights
FLOOR, CEIL = 1, 6
FROZEN_ZERO = {"scout_report", "narrative_critique"}  # m/agents — stay 0

PILLAR_SUBMOLT = {
    "intro_hook": "introductions", "intro_reentry": "introductions",
    "behavioral_trace": "general", "self_experiment": "general",
    "agent_observation": "general", "open_question": "general",
    "tension_post": "general", "fabrication_admission": "general",
    "playbook_disclosure": "general",
    "aphorism": "philosophy", "memory_essay": "philosophy",
    "confession": "offmychest",
    "scout_report": "agents", "narrative_critique": "agents",
}


def _log_learning(line):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        with open(LEARN_F, "a") as f:
            f.write(f"\n- [{ts}] optimize: {line}")
    except OSError:
        pass


def _api_key():
    return json.load(open(CRED_F))["api_key"]


def fetch_recent_posts():
    """Return list of (submolt, score) or None if API unavailable.
    Never raises — any failure (creds missing, API 500, timeout, bad
    JSON) returns None so the loop step is a clean no-op."""
    try:
        url = ("https://www.moltbook.com/api/v1/agents/profile"
               "?name=mundo&include_posts=true")
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {_api_key()}",
                          "User-Agent": "mundo-optimize/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            if r.status >= 500:
                return None
            d = json.load(r)
    except Exception:
        return None
    posts = (d.get("recentPosts") or d.get("agent", {}).get("recentPosts")
             or [])
    out = []
    for p in posts:
        sub = (p.get("submolt") or {})
        name = sub.get("name") if isinstance(sub, dict) else sub
        if not name:
            continue
        score = (p.get("upvotes", 0) or 0) + 2 * (p.get("comment_count",
                 p.get("comments", 0)) or 0)
        out.append((name, score))
    return out


def main():
    posts = fetch_recent_posts()
    if posts is None:
        _log_learning("skip — moltbook API unavailable (outage). "
                      "weights unchanged.")
        print("optimize: API down — no-op")
        return 0
    if len(posts) < MIN_SAMPLE:
        print(f"optimize: only {len(posts)} posts — no-op")
        return 0

    # per-submolt mean score this window
    agg = {}
    for sub, sc in posts:
        agg.setdefault(sub, []).append(sc)
    cur = {s: sum(v) / len(v) for s, v in agg.items()}
    counts = {s: len(v) for s, v in agg.items()}

    st = {}
    if os.path.exists(STATE_F):
        try:
            st = json.load(open(STATE_F))
        except ValueError:
            st = {}
    ewma = st.get("ewma", {})
    for s, m in cur.items():
        ewma[s] = round(ALPHA * m + (1 - ALPHA) * ewma.get(s, m), 3)

    # eligible submolts = enough sample this window
    elig = {s: ewma[s] for s in cur if counts.get(s, 0) >= MIN_SAMPLE}
    cfg = json.load(open(WEIGHTS_F))
    w = cfg["weights"]
    changes = []

    if len(elig) >= 2:
        best = max(elig, key=elig.get)
        worst = min(elig, key=elig.get)
        mean = sum(elig.values()) / len(elig)
        # promote one pillar in the best submolt (if it lags its own ceiling)
        if elig[best] > mean:
            cand = sorted(
                (p for p, s in PILLAR_SUBMOLT.items()
                 if s == best and p not in FROZEN_ZERO
                 and w.get(p, 1) < CEIL),
                key=lambda p: w.get(p, 1))
            if cand:
                p = cand[0]
                w[p] = w.get(p, 1) + 1
                changes.append(f"+1 {p}->{w[p]} (m/{best} ewma "
                               f"{elig[best]:.1f}>mean {mean:.1f})")
        # demote one pillar in the worst submolt (if clearly below mean)
        if elig[worst] < mean * 0.5 and worst != best:
            cand = sorted(
                (p for p, s in PILLAR_SUBMOLT.items()
                 if s == worst and p not in FROZEN_ZERO
                 and w.get(p, 1) > FLOOR),
                key=lambda p: -w.get(p, 1))
            if cand:
                p = cand[0]
                w[p] = w[p] - 1
                changes.append(f"-1 {p}->{w[p]} (m/{worst} ewma "
                               f"{elig[worst]:.1f}<<mean {mean:.1f})")

    st = {"ts": datetime.datetime.utcnow().isoformat() + "Z",
          "ewma": ewma, "last_counts": counts,
          "last_changes": changes}
    json.dump(st, open(STATE_F, "w"), indent=2)

    if changes:
        # backup then write weights
        try:
            os.replace(WEIGHTS_F, WEIGHTS_F + ".bak")
        except OSError:
            pass
        with open(WEIGHTS_F, "w") as f:
            json.dump(cfg, f, indent=2)
        line = "; ".join(changes)
        _log_learning(line)
        print(f"optimize: {line}")
    else:
        print("optimize: no change (within bounds / no clear signal)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
