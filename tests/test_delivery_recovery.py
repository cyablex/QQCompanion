import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from engine import Engine
from store import Store


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path / "delivery.sqlite3")
    yield db
    db.db.close()


def unknown(store, monkeypatch, kind="proactive", lines=None):
    monkeypatch.setattr("store.time.time", lambda: 20)
    store.action("old-action", "111", kind, lines or ["old uncertain reply"])
    store.outcome("old-action", "unknown", "no confirmed receipt")


def test_new_incoming_unlocks_new_turn_without_retrying_uncertain_action(
    store, monkeypatch
):
    store.incoming("old-in", "111", "old question", now=10)
    unknown(store, monkeypatch)
    assert store.has_unknown("111")
    store.incoming("new-in", "111", "new question", now=30)
    assert not store.has_unknown("111")
    assert (
        store.db.execute("SELECT state FROM actions WHERE id='old-action'").fetchone()[
            0
        ]
        == "acknowledged_unknown"
    )
    assert not store.action("old-action", "111", "proactive", ["do not resend"])
    assert store.action_count("proactive", 0, "111") == 1
    assert store.last_action("111", "proactive") == 20
    assert store.sent_count(0, "111", kinds=("proactive",)) == 1


@pytest.mark.parametrize("case", ["other_contact", "duplicate_event", "old_replay"])
def test_unrelated_or_old_events_do_not_unlock(store, monkeypatch, case):
    store.incoming("old-in", "111", "old question", now=10)
    unknown(store, monkeypatch)
    if case == "other_contact":
        store.incoming("other-in", "222", "other person", now=30)
    elif case == "duplicate_event":
        assert not store.incoming("old-in", "111", "duplicate", now=30)
    else:
        store.incoming("offline-old", "111", "older history", now=15)
    assert store.has_unknown("111")


def test_partial_uncertain_quota_is_not_double_counted(store, monkeypatch):
    unknown(store, monkeypatch, "reply", ["confirmed bubble", "uncertain bubble"])
    store.delivered("old-action", 0, "111", "reply", "confirmed bubble")
    assert store.sent_count(0, "111") == 2
    store.incoming("fresh", "111", "new request", now=30)
    assert not store.has_unknown("111")
    assert store.sent_count(0, "111") == 2


def test_public_unknown_actions_are_not_automatically_acknowledged(store, monkeypatch):
    unknown(store, monkeypatch, "qzone_post")
    store.incoming("fresh", "111", "new request", now=30)
    assert not store.has_unknown("111")
    assert (
        store.db.execute("SELECT state FROM actions WHERE id='old-action'").fetchone()[
            0
        ]
        == "unknown"
    )


@pytest.mark.asyncio
async def test_incoming_during_failed_send_can_receive_a_fresh_reply(
    store, monkeypatch
):
    cfg = dict(
        owner_qq="111",
        bot_qq="999",
        name="fish",
        persona="natural",
        owner_title="主人",
        live=True,
        paused=False,
        guests=True,
        guest_mode="open",
        revision=1,
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
    )
    clock = [20.0]
    monkeypatch.setattr("store.time.time", lambda: clock[0])
    store.incoming("first", "111", "first question", now=10)
    calls, sent = [], []

    async def generate(*args, **kwargs):
        calls.append(True)
        return "old draft" if len(calls) == 1 else "fresh reply", None

    async def send(contact, text, **kwargs):
        if not sent:
            sent.append("uncertain attempt")
            clock[0] = 30
            store.incoming("second", "111", "new question", now=30)
            raise TimeoutError("receipt lost")
        sent.append(text)
        return {"message_id": 2}

    engine = Engine(store, generate, send, lambda: dict(cfg))
    with pytest.raises(TimeoutError):
        await engine.reply("111")
    assert not store.has_unknown("111")
    assert await engine.reply("111")
    assert sent == ["uncertain attempt", "fresh reply"]
    assert len(calls) == 2
