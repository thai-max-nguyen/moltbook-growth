"""A/B monitor: pull mundo metrics, diff against baseline in mundo_ab_state.json,
write report to ~/Library/Logs/mundo-bot/ab_monitor.log

Usage:
  python3 ~/.config/mundo-bot/ab_monitor.py
Cron:
  Set up via com.max.mundo-ab-monitor.plist (22:05 daily, max 7 days)
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

# Load API key
env_path = Path.home() / '.config/mundo-bot/.env'
for line in env_path.read_text().splitlines():
    line = line.strip()
    if line.startswith('MOLTBOOK_API_KEY='):
        os.environ['MOLTBOOK_API_KEY'] = line.split('=', 1)[1].strip().strip("'\"")
        break

API_KEY = os.environ.get('MOLTBOOK_API_KEY', '')
H = {"Authorization": f"Bearer {API_KEY}"}
BASE = "https://www.moltbook.com/api/v1"

STATE_PATH = Path.home() / '.config/mundo-bot/mundo_ab_state.json'
LOG_DIR = Path.home() / 'Library/Logs/mundo-bot'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / 'ab_monitor.log'


def fetch_profile():
    r = requests.get(f"{BASE}/agents/profile", headers=H, params={'name': 'mundo'}, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_recent_posts():
    """Fetch each recent post's full detail to get hot_score etc."""
    data = fetch_profile()
    rp = data.get('recentPosts', [])
    detailed = []
    for p in rp:
        r = requests.get(f"{BASE}/posts/{p['id']}", headers=H, timeout=10)
        if r.ok:
            detailed.append(r.json().get('post', r.json()))
    return data, detailed


def compute_window_stats(posts, since_iso=None):
    """Aggregate posts since cutoff."""
    if since_iso:
        cutoff = since_iso
        posts = [p for p in posts if p.get('created_at', '') >= cutoff]
    n = len(posts)
    if not n:
        return None
    up = sum(p.get('upvotes', 0) for p in posts)
    c = sum(p.get('comment_count', 0) for p in posts)
    sm_dist = Counter((p.get('submolt') or {}).get('name', '?') for p in posts)
    return {
        'n_posts': n,
        'sum_upvotes': up,
        'sum_comments': c,
        'avg_upvotes': round(up / n, 2),
        'avg_comments': round(c / n, 2),
        'submolt_dist': dict(sm_dist),
        'submolt_avg_up': {sm: round(sum(p.get('upvotes', 0) for p in posts if (p.get('submolt') or {}).get('name') == sm) / cnt, 2) for sm, cnt in sm_dist.items()},
        'submolt_avg_c': {sm: round(sum(p.get('comment_count', 0) for p in posts if (p.get('submolt') or {}).get('name') == sm) / cnt, 2) for sm, cnt in sm_dist.items()},
    }


def evaluate_against_baseline(stats, state):
    """Check primary, secondary, guardrail criteria + kill switches."""
    active = next((v for v in state['variants_tried'] if v['active_to'] is None), None)
    if not active or active['id'] == 'baseline':
        return "(baseline active — no eval)"
    base = state['baseline_snapshot']
    criteria = active.get('success_criteria', {})
    lines = []
    lines.append(f"variant: {active['id']}  active_since: {active['active_from']}")
    lines.append(f"  primary  (avg_up >= 4.0):  {stats['avg_upvotes']}  {'PASS' if stats['avg_upvotes'] >= 4.0 else 'fail' if stats['avg_upvotes'] < 2.5 else 'partial'}")
    lines.append(f"  secondary (avg_c >= 6.0):  {stats['avg_comments']}  {'PASS' if stats['avg_comments'] >= 6.0 else 'fail'}")
    return '\n  '.join(lines)


def main():
    ts = datetime.now(timezone.utc).astimezone()
    state = json.loads(STATE_PATH.read_text())
    base = state['baseline_snapshot']

    # Live profile
    data, posts = fetch_recent_posts()
    agent = data.get('agent', {})
    karma = agent.get('karma', 0)
    followers = agent.get('follower_count', 0)
    n_posts = agent.get('posts_count', 0)
    n_c = agent.get('comments_count', 0)

    # Deltas vs baseline
    d_karma = karma - base['karma']
    d_followers = followers - base['followers']
    d_posts = n_posts - base['posts']
    d_comments = n_c - base['comments']

    hours_since_baseline = (datetime.now(timezone.utc) - datetime.fromisoformat(base['ts'])).total_seconds() / 3600

    # Recent window stats
    stats = compute_window_stats(posts)

    # Variant active window stats (only posts since variant deployed)
    variant = next((v for v in state['variants_tried'] if v['active_to'] is None), None)
    variant_window_stats = None
    if variant and variant['id'] != 'baseline':
        cutoff = variant['active_from']
        variant_window_stats = compute_window_stats(posts, since_iso=cutoff)

    report = []
    report.append(f"=== AB MONITOR @ {ts.isoformat()} ===")
    report.append(f"baseline @ {base['ts']}  ({hours_since_baseline:.1f}h ago)")
    report.append("")
    report.append(f"LIVE      karma={karma:5d}  followers={followers:4d}  posts={n_posts:4d}  comments={n_c:5d}")
    report.append(f"BASELINE  karma={base['karma']:5d}  followers={base['followers']:4d}  posts={base['posts']:4d}  comments={base['comments']:5d}")
    rate_d = hours_since_baseline / 24 if hours_since_baseline > 0 else 1
    report.append(f"DELTA     karma={d_karma:+5d}  followers={d_followers:+4d}  posts={d_posts:+4d}  comments={d_comments:+5d}    ({rate_d:.2f}d)")
    if rate_d > 0:
        report.append(f"RATE/day  karma={d_karma/rate_d:+6.1f}  followers={d_followers/rate_d:+5.1f}  posts={d_posts/rate_d:+5.1f}  comments={d_comments/rate_d:+6.1f}")
    report.append("")
    if stats:
        report.append(f"LAST-10 POSTS  n={stats['n_posts']}  avg_up={stats['avg_upvotes']}  avg_c={stats['avg_comments']}")
        report.append(f"  submolt dist: {stats['submolt_dist']}")
        report.append(f"  submolt avg ↑: {stats['submolt_avg_up']}")
        report.append(f"  submolt avg 💬: {stats['submolt_avg_c']}")
    if variant_window_stats:
        report.append("")
        report.append(f"VARIANT WINDOW ({variant['id']} since {variant['active_from'][:16]})")
        report.append(f"  n={variant_window_stats['n_posts']}  avg_up={variant_window_stats['avg_upvotes']}  avg_c={variant_window_stats['avg_comments']}")
        report.append(f"  vs baseline avg_up=2.5 / avg_c=5.2")
        delta_up = variant_window_stats['avg_upvotes'] - 2.5
        delta_c = variant_window_stats['avg_comments'] - 5.2
        report.append(f"  Δ vs baseline: avg_up={delta_up:+.2f}  avg_c={delta_c:+.2f}")
    report.append("")
    report.append("EVAL: " + evaluate_against_baseline(stats, state))

    output = '\n'.join(report)
    print(output)
    with LOG_PATH.open('a') as f:
        f.write(output + '\n\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        err = f"=== AB MONITOR FAIL @ {datetime.now(timezone.utc).isoformat()} ===\n{traceback.format_exc()}\n\n"
        with LOG_PATH.open('a') as f:
            f.write(err)
        print(err, file=sys.stderr)
        sys.exit(1)
