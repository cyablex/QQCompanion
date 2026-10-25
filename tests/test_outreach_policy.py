import sys
from datetime import datetime
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from outreach_policy import assessment_due, contact_cap, contacted_friends, friend_target
from store import Store, TZ


def test_default_can_rise_to_three_without_overwriting_explicit_two():
    cfg = dict(owner_qq="11111", proactive_max=6, friend_proactive_daily_max=3)
    assert contact_cap(cfg, "33333", {"daily_cap": 2, "explicit_cap": None}) == 3
    assert contact_cap(cfg, "33333", {"daily_cap": 2, "explicit_cap": 2}) == 2
    assert contact_cap(cfg, "33333", {"daily_cap": 0, "explicit_cap": 0}) == 0
    assert contact_cap(cfg, "11111", {"daily_cap": 2, "explicit_cap": None}) == 6


def test_boot_wait_is_five_to_twelve_minutes_and_friend_assessments_are_spaced(tmp_path):
    store = Store(tmp_path / "timing.sqlite3")
    cfg = {"bot_qq": "22222"}
    assert not assessment_due(store, cfg, "friends", 1000, reconnected=True)
    due = store.get("outreach_due:22222:friends")
    assert 1300 <= due <= 1720
    assert not assessment_due(store, cfg, "friends", due - 1)
    assert assessment_due(store, cfg, "friends", due)
    next_due = store.get("outreach_due:22222:friends")
    assert due + 900 <= next_due <= due + 1800
    # A reconnect gives one quiet startup window rather than sending overdue pings at once.
    assert not assessment_due(store, cfg, "friends", next_due + 100, reconnected=True)
    store.db.close()


def test_daily_unique_friend_goal_persists_and_uncertain_sends_count(tmp_path):
    store = Store(tmp_path / "target.sqlite3")
    cfg = {"bot_qq": "22222"}
    now = datetime.now(TZ).replace(hour=12).timestamp()
    target = friend_target(store, cfg, now)
    assert target in {3, 4} and friend_target(store, cfg, now) == target
    for contact in ["33333", "44444"]:
        key = "uncertain:" + contact
        store.action(key, contact, "friend_proactive", ["hello"])
        store.outcome(key, "unknown")
        store.db.execute("UPDATE actions SET created=? WHERE id=?", (now, key))
    assert contacted_friends(store, now) == {"33333", "44444"}
    assert contacted_friends(store, now + 86400) == set()
    store.db.close()
