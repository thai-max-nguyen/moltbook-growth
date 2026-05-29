#!/usr/bin/env python3
"""Tests for mundo_ab_closer.py — variant lifecycle controller.

Validates: kill-switch detection, success-criterion parsing, queue rotation,
days-since math. No live API calls — all stats injected via mocks.
"""
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.config/mundo-bot"))
import mundo_ab_closer as ab


def _iso(dt):
    return dt.astimezone().isoformat(timespec="minutes")


class DaysSince(unittest.TestCase):
    def test_one_day_ago(self):
        ts = _iso(datetime.now(timezone.utc) - timedelta(days=1))
        self.assertAlmostEqual(ab._days_since(ts), 1.0, delta=0.05)

    def test_none_returns_zero(self):
        self.assertEqual(ab._days_since(None), 0.0)

    def test_unparseable_returns_zero(self):
        self.assertEqual(ab._days_since("not-a-date"), 0.0)


class KillSwitch(unittest.TestCase):
    def _state(self, deployed_days_ago, baseline_followers=72):
        return {
            "current_variant": "test_v",
            "deployed_at": _iso(datetime.now(timezone.utc) - timedelta(days=deployed_days_ago)),
            "baseline_snapshot": {
                "karma": 657, "followers": baseline_followers, "posts": 218, "comments": 2251,
            },
            "variants_tried": [
                {"id": "test_v",
                 "active_from": _iso(datetime.now(timezone.utc) - timedelta(days=deployed_days_ago)),
                 "active_to": None,
                 "success_criteria": {"min_days": 5, "min_posts_in_window": 25,
                                      "primary": "avg_upvotes_per_post >= 4.0"}}],
        }

    def test_followers_below_1_per_day_after_3d_trips(self):
        state = self._state(deployed_days_ago=4)
        metrics = {"karma": 700, "followers": 75, "posts": 240,
                   "comments": 2400, "recent_posts": [{"upvotes": 5, "comment_count": 5}]}
        reason = ab._check_kill_switches(state, metrics)
        self.assertIsNotNone(reason)
        self.assertIn("followers/day", reason)

    def test_healthy_growth_does_not_trip(self):
        state = self._state(deployed_days_ago=4)
        metrics = {"karma": 800, "followers": 80, "posts": 250,
                   "comments": 2500, "recent_posts": [{"upvotes": 5, "comment_count": 5}]}
        self.assertIsNone(ab._check_kill_switches(state, metrics))

    def test_avg_upvotes_below_1_5_after_2d_trips(self):
        state = self._state(deployed_days_ago=3)
        metrics = {"karma": 700, "followers": 80, "posts": 240, "comments": 2400,
                   "recent_posts": [{"upvotes": 1, "comment_count": 1}] * 10}
        reason = ab._check_kill_switches(state, metrics)
        self.assertIsNotNone(reason)
        self.assertIn("avg_upvotes", reason)


class SuccessCriteria(unittest.TestCase):
    def _state(self, days_ago, posts_now, recent_posts_perf):
        return {
            "baseline_snapshot": {"karma": 657, "followers": 72, "posts": 218, "comments": 2251},
            "variants_tried": [
                {"id": "v",
                 "active_from": _iso(datetime.now(timezone.utc) - timedelta(days=days_ago)),
                 "active_to": None,
                 "success_criteria": {"min_days": 5, "min_posts_in_window": 25,
                                      "primary": "avg_upvotes_per_post >= 4.0"}}],
        }, {"karma": 800, "followers": 80, "posts": posts_now, "comments": 2400,
             "recent_posts": [{"upvotes": u, "comment_count": c} for u, c in recent_posts_perf]}

    def test_passes_when_all_three_gates_met(self):
        state, metrics = self._state(
            days_ago=6, posts_now=250, recent_posts_perf=[(5, 7)] * 10)
        self.assertTrue(ab._check_success(state, metrics))

    def test_fails_when_min_days_not_reached(self):
        state, metrics = self._state(
            days_ago=3, posts_now=260, recent_posts_perf=[(5, 7)] * 10)
        self.assertFalse(ab._check_success(state, metrics))

    def test_fails_when_min_posts_not_reached(self):
        state, metrics = self._state(
            days_ago=6, posts_now=220, recent_posts_perf=[(5, 7)] * 10)
        self.assertFalse(ab._check_success(state, metrics))

    def test_fails_when_primary_metric_below_target(self):
        state, metrics = self._state(
            days_ago=6, posts_now=250, recent_posts_perf=[(2, 5)] * 10)
        self.assertFalse(ab._check_success(state, metrics))


class QueueRotation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "ab_state.json"
        self.weights_path = Path(self.tmp.name) / "pillar_weights.json"
        self._patches = [
            mock.patch.object(ab, "AB_STATE", self.state_path),
            mock.patch.object(ab, "WEIGHTS", self.weights_path),
            mock.patch.object(ab, "LEARNINGS", Path(self.tmp.name) / "learnings.md"),
            mock.patch.object(ab, "LOG", Path(self.tmp.name) / "log"),
        ]
        for p in self._patches: p.start()

    def tearDown(self):
        for p in self._patches: p.stop()
        self.tmp.cleanup()

    def test_activate_next_pops_queue_and_deploys(self):
        state = {
            "current_variant": "old",
            "variants_tried": [],
            "next_variant_queue": [
                {"id": "new", "weights": {"behavioral_trace": 5}, "hypothesis": "h"},
                {"id": "later", "weights": {"behavioral_trace": 3}, "hypothesis": "h2"},
            ],
            "baseline_snapshot": {},
        }
        self.weights_path.write_text(json.dumps({"weights": {"behavioral_trace": 1}}))
        ok = ab._activate_next(state, {"karma": 100, "followers": 10, "posts": 5, "comments": 50})
        self.assertTrue(ok)
        self.assertEqual(len(state["next_variant_queue"]), 1)
        self.assertEqual(state["next_variant_queue"][0]["id"], "later")
        self.assertEqual(state["current_variant"], "new")
        deployed = json.loads(self.weights_path.read_text())
        self.assertEqual(deployed["weights"]["behavioral_trace"], 5)

    def test_empty_queue_returns_false_without_crashing(self):
        state = {"variants_tried": [], "next_variant_queue": [], "baseline_snapshot": {}}
        self.assertFalse(ab._activate_next(state, {"karma": 100}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
