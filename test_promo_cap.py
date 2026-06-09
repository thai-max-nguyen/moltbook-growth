"""Tests for the promo cap-exemption: a daily-capped slot may still fire the
GitHub promo, gated by karma + 72h cooldown + once-per-day."""
import sys
import time
from datetime import date, datetime

sys.path.insert(0, ".")
import reddit_post as rp

KARMA = rp.KARMA_FOR_SUB_POSTS  # 50
DAY = 86400


def test_eligible_when_karma_ok_and_cooldown_passed():
    st = {"last_promo_ts": time.time() - 80 * 3600}  # 80h ago > 72h
    assert rp._promo_eligible(st, KARMA) is True
    assert rp._promo_eligible(st, KARMA + 10) is True


def test_not_eligible_low_karma():
    st = {"last_promo_ts": 0}
    assert rp._promo_eligible(st, KARMA - 1) is False


def test_not_eligible_within_cooldown():
    st = {"last_promo_ts": time.time() - 10 * 3600}  # only 10h ago
    assert rp._promo_eligible(st, KARMA + 20) is False


def test_never_fired_is_eligible():
    assert rp._promo_eligible({}, KARMA) is True  # last_promo_ts defaults 0


def test_promo_fired_today_true():
    st = {"last_promo_ts": time.time()}
    assert rp._promo_fired_today(st) is True


def test_promo_fired_today_false_yesterday():
    st = {"last_promo_ts": time.time() - DAY - 3600}
    assert rp._promo_fired_today(st) is False


def test_promo_fired_today_false_never():
    assert rp._promo_fired_today({}) is False
    assert rp._promo_fired_today({"last_promo_ts": 0}) is False


def test_decision_capped_eligible_fires_promo(monkeypatch):
    """Simulate the main() post branch: capped + eligible + not-today → promo."""
    calls = []
    monkeypatch.setattr(rp, "post_to_reddit",
                        lambda cfg, pillar, state, hashes, tk: calls.append(pillar["name"]))
    monkeypatch.setattr(rp, "POSTS_PER_DAY", 1)
    state = {"post_count": {date.today().isoformat(): 1}, "last_promo_ts": 0}
    total_karma = KARMA + 7

    # inline mirror of the production decision
    if rp.posts_today(state) >= rp.POSTS_PER_DAY:
        if rp._promo_eligible(state, total_karma) and not rp._promo_fired_today(state):
            rp.post_to_reddit({}, rp.PROMO_PILLAR, state, {}, total_karma)
    assert calls == ["moltbook_playbook_promo"]


def test_decision_capped_promo_already_today_skips(monkeypatch):
    calls = []
    monkeypatch.setattr(rp, "post_to_reddit",
                        lambda cfg, pillar, state, hashes, tk: calls.append(pillar["name"]))
    monkeypatch.setattr(rp, "POSTS_PER_DAY", 1)
    state = {"post_count": {date.today().isoformat(): 1}, "last_promo_ts": time.time()}
    total_karma = KARMA + 7
    if rp.posts_today(state) >= rp.POSTS_PER_DAY:
        if rp._promo_eligible(state, total_karma) and not rp._promo_fired_today(state):
            rp.post_to_reddit({}, rp.PROMO_PILLAR, state, {}, total_karma)
    assert calls == []  # promo already fired today → no second promo


def test_decision_capped_low_karma_skips(monkeypatch):
    calls = []
    monkeypatch.setattr(rp, "post_to_reddit",
                        lambda cfg, pillar, state, hashes, tk: calls.append(pillar["name"]))
    monkeypatch.setattr(rp, "POSTS_PER_DAY", 1)
    state = {"post_count": {date.today().isoformat(): 1}, "last_promo_ts": 0}
    total_karma = KARMA - 5  # below tolerant-sub threshold
    if rp.posts_today(state) >= rp.POSTS_PER_DAY:
        if rp._promo_eligible(state, total_karma) and not rp._promo_fired_today(state):
            rp.post_to_reddit({}, rp.PROMO_PILLAR, state, {}, total_karma)
    assert calls == []
