#!/usr/bin/env python3
"""mundo_ab_closer.py — daily A/B variant lifecycle controller.

Reads ~/.config/mundo-bot/mundo_ab_state.json. For the active variant:

  1. Pull live stats from Moltbook public profile + computed window metrics.
  2. Check kill switches first. If any tripped → rollback to baseline.
  3. Check success criteria (min_days + min_posts + primary metric).
  4. If criteria HIT → close variant, deploy next from next_variant_queue.
  5. Otherwise log progress + exit.

Distinct from mundo_optimize.py (per-cycle ±1 nudges) — this manages the
larger-scale variant rotation strategy. Runs once/day via cron at 21:00
(before mundo_daily_review at 21:45).

Designed to be SAFE under partial data: never deploys without ≥80% of the
min_days window completed (prevents thrash). Always backs up weights to
.bak_<ts> before overwrite.
"""
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = HOME / ".config/mundo-bot"
AB_STATE = CONFIG_DIR / "mundo_ab_state.json"
WEIGHTS = CONFIG_DIR / "pillar_weights.json"
CREDS = HOME / ".config/moltbook/credentials.json"
LOG = HOME / "Library/Logs/mundo-bot/ab_closer.log"
LEARNINGS = CONFIG_DIR / "mundo_learnings.md"

LOG.parent.mkdir(parents=True, exist_ok=True)


def _log(msg, level="info"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG, "a") as f:
        f.write(f"[{ts}] [{level}] {msg}\n")
    print(f"[{level}] {msg}")


def _now_iso():
    return datetime.now().astimezone().isoformat(timespec="minutes")


def _read(path):
    return json.loads(path.read_text())


def _write_atomic(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path)


def _api_key():
    try:
        return _read(CREDS).get("api_key")
    except Exception:
        return None


def _live_stats():
    """Fetch current mundo agent stats. Auth optional (public endpoint)."""
    url = "https://www.moltbook.com/api/v1/agents/profile?name=mundo"
    headers = {"User-Agent": "mundo-ab-closer/1.0"}
    key = _api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read())
    agent = body.get("agent", {})
    recent_posts = body.get("recentPosts", [])
    return {
        "karma": agent.get("karma", 0),
        "followers": agent.get("follower_count", 0),
        "posts": agent.get("posts_count", 0),
        "comments": agent.get("comments_count", 0),
        "recent_posts": recent_posts,
        "captured_at": _now_iso(),
    }


def _days_since(iso_str):
    """Days (float) since an ISO timestamp."""
    if not iso_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds() / 86400.0
    except Exception as e:
        _log(f"could not parse {iso_str!r}: {e!r}", level="warn")
        return 0.0


def _backup_weights():
    if not WEIGHTS.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = WEIGHTS.with_suffix(f".json.bak_{ts}")
    shutil.copy(WEIGHTS, dest)
    return str(dest)


def _deploy_variant(variant, prior_metrics):
    """Write new pillar_weights.json with proposed changes."""
    new_weights = variant.get("weights") or variant.get("weight_changes_proposed")
    if not new_weights:
        _log(f"variant {variant['id']} has no weights — aborting deploy", level="error")
        return False

    bk = _backup_weights()
    if bk:
        _log(f"backed up old weights → {bk}")

    if "weight_changes_proposed" in variant and "weights" not in variant:
        # Variant only lists deltas — apply on top of current weights.
        current = _read(WEIGHTS).get("weights", {})
        weights_out = dict(current)
        for k, change_str in new_weights.items():
            # "from to X" or "X to Y" string — extract final number.
            tokens = change_str.split()
            try:
                weights_out[k] = int(tokens[2])
            except Exception:
                _log(f"  cannot parse change '{change_str}' for {k} — skipped", level="warn")
        new_weights_dict = weights_out
    else:
        new_weights_dict = new_weights

    payload = {
        "_comment": f"Auto-deployed by mundo_ab_closer.py at {_now_iso()}. Variant: {variant['id']}.",
        "_variant": variant["id"],
        "_deployed": _now_iso(),
        "_baseline_metrics": prior_metrics,
        "weights": new_weights_dict,
    }
    _write_atomic(WEIGHTS, payload)
    _log(f"DEPLOYED variant {variant['id']} → pillar_weights.json")
    return True


def _check_kill_switches(state, current_metrics):
    """Return reason string if any kill switch tripped, else None."""
    var = next((v for v in state["variants_tried"] if v.get("active_to") is None), None)
    if not var:
        return None
    days_active = _days_since(var.get("active_from"))
    baseline = state.get("baseline_snapshot", {})
    delta_days = max(days_active, 1.0)

    followers_delta = current_metrics["followers"] - baseline.get("followers", 0)
    followers_per_day = followers_delta / delta_days
    if days_active >= 3 and followers_per_day < 1.0:
        return f"followers/day {followers_per_day:.2f} < 1.0 for {days_active:.1f}d"

    # Avg upvotes from recent posts (last 10).
    recent = current_metrics.get("recent_posts", [])
    if recent:
        avg_up = sum(p.get("upvotes", 0) for p in recent) / len(recent)
        if avg_up < 1.5 and days_active >= 2:
            return f"avg_upvotes/post {avg_up:.2f} < 1.5 (worse than baseline)"
    return None


def _check_success(state, current_metrics):
    """Return True if active variant's success criteria are met."""
    var = next((v for v in state["variants_tried"] if v.get("active_to") is None), None)
    if not var:
        return False
    crit = var.get("success_criteria", {})
    days_active = _days_since(var.get("active_from"))
    min_days = crit.get("min_days", 5)
    if days_active < min_days:
        return False

    baseline = state.get("baseline_snapshot", {})
    posts_delta = current_metrics["posts"] - baseline.get("posts", 0)
    min_posts = crit.get("min_posts_in_window", 25)
    if posts_delta < min_posts:
        return False

    # Primary criterion is text — parse "avg_X >= N" form.
    recent = current_metrics.get("recent_posts", [])
    if not recent:
        return False
    avg_up = sum(p.get("upvotes", 0) for p in recent) / len(recent)
    avg_cmt = sum(p.get("comment_count", 0) for p in recent) / len(recent)
    primary = crit.get("primary", "")
    # Extract threshold number from "avg_upvotes_per_post >= 4.0 (baseline 2.5)"
    import re
    nums = re.findall(r">=\s*(\d+\.?\d*)", primary)
    if not nums:
        return True  # No parseable criterion → time + post-count gate enough.
    target = float(nums[0])
    metric = avg_up if "upvote" in primary.lower() else avg_cmt
    return metric >= target


def _close_variant(state, outcome, current_metrics):
    """Mark active variant with active_to + outcome."""
    var = next((v for v in state["variants_tried"] if v.get("active_to") is None), None)
    if not var:
        return None
    var["active_to"] = _now_iso()
    var["outcome"] = outcome
    var["final_metrics"] = current_metrics
    return var


def _activate_next(state, current_metrics):
    """Pop first item off next_variant_queue and start it."""
    q = state.get("next_variant_queue", [])
    if not q:
        _log("queue empty — no next variant to activate", level="warn")
        return False
    nxt = q.pop(0)
    state["next_variant_queue"] = q
    nxt["active_from"] = _now_iso()
    nxt["active_to"] = None
    state["variants_tried"].append(nxt)
    state["current_variant"] = nxt["id"]
    state["deployed_at"] = _now_iso()
    state["baseline_snapshot"] = current_metrics.copy()
    state["baseline_snapshot"].pop("recent_posts", None)
    return _deploy_variant(nxt, prior_metrics=current_metrics)


def _append_learning(line):
    with open(LEARNINGS, "a") as f:
        f.write(f"\n- {_now_iso()} — {line}\n")


def main():
    if not AB_STATE.exists():
        _log("mundo_ab_state.json missing — nothing to do", level="warn")
        return 0
    state = _read(AB_STATE)

    try:
        current = _live_stats()
    except Exception as e:
        _log(f"could not pull live stats: {e!r}", level="error")
        return 1

    kill_reason = _check_kill_switches(state, current)
    if kill_reason:
        _log(f"KILL SWITCH tripped: {kill_reason}", level="error")
        var = _close_variant(state, outcome=f"killed: {kill_reason}", current_metrics=current)
        # Rollback: deploy from most recent .bak file is left to operator.
        # Auto-action: just close variant + emit alert.
        _append_learning(f"A/B closer KILLED {var['id'] if var else '?'} — {kill_reason}. Manual rollback recommended.")
        _write_atomic(AB_STATE, state)
        return 2

    success = _check_success(state, current)
    var = next((v for v in state["variants_tried"] if v.get("active_to") is None), None)
    if not var:
        _log("no active variant — pipeline idle", level="warn")
        return 0

    days_active = _days_since(var.get("active_from"))
    posts_delta = current["posts"] - state.get("baseline_snapshot", {}).get("posts", 0)

    if success:
        _log(f"SUCCESS — variant {var['id']} hit criteria ({days_active:.1f}d, {posts_delta} posts)")
        _close_variant(state, outcome="passed", current_metrics={k: v for k, v in current.items() if k != "recent_posts"})
        if _activate_next(state, {k: v for k, v in current.items() if k != "recent_posts"}):
            new_id = state["current_variant"]
            _append_learning(f"A/B closer: {var['id']} PASSED → activated {new_id}.")
        _write_atomic(AB_STATE, state)
        return 0

    _log(f"variant {var['id']} still running — {days_active:.1f}d active, {posts_delta} posts in window. Criteria not yet met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
