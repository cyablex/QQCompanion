import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import friend_proactive
import store as store_module
import engine as engine_module
from engine import Engine
from friend_proactive import FriendProactive
from policy import day_start
from store import Store, TZ, CHAT_KINDS


def test_live_incoming_markers_differ_when_windows_clock_is_identical(env, monkeypatch):
    workflow, store, cfg, engine, now, sent, calls = env
    monkeypatch.setattr(store_module.time, "time", lambda: now)
    store.incoming("same-clock-1", "11111", "one")
    before = store.last_incoming("11111")
    store.incoming("same-clock-2", "11111", "two")
    assert store.last_incoming("11111") > before


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = dict(
        owner_qq="11111",
        bot_qq="22222",
        name="fish",
        persona="natural",
        owner_title="主人",
        revision=1,
        live=True,
        paused=False,
        guests=True,
        guest_mode="open",
        proactive=True,
        friend_proactive=True,
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
        daily_chat_messages=200,
        guest_daily_messages=20,
    )
    store = Store(tmp_path / "friends.sqlite3")
    now = (
        datetime.now(TZ).replace(hour=22, minute=0, second=0, microsecond=0).timestamp()
    )
    monkeypatch.setattr(store_module.time, "time", lambda: now)
    store.set("known_friend_ids", ["33333"])
    store.incoming("old", "33333", "I like rice", now - 5 * 3600)
    store.message("33333", "assistant", "noted", now - 5 * 3600 + 1)
    sent, calls = [], []

    async def generate(system, contexts, **kwargs):
        calls.append((system, contexts))
        return (
            '{"action":"reply","messages":["one grounded follow-up"],"memory":[]}',
            None,
        )

    async def send(contact, text):
        sent.append((contact, text))
        return {"message_id": len(sent)}

    engine = Engine(store, generate, send, lambda: cfg)
    plugin = SimpleNamespace(store=store, engine=engine, workers={})
    workflow = FriendProactive(plugin)
    monkeypatch.setattr(friend_proactive, "read_settings", lambda: cfg)
    monkeypatch.setattr(engine_module, "quiet", lambda moment: False)
    yield workflow, store, cfg, engine, now, sent, calls
    store.db.close()


@pytest.mark.asyncio
async def test_friend_proactive_at_most_once_counts_both_limits_and_no_chasing(env):
    workflow, store, cfg, engine, now, sent, calls = env
    assert await workflow.run(now)
    assert len(sent) == 1 and sent[0][0] == "33333"
    assert not await workflow.run(now + 1)
    assert store.sent_count(day_start(), "33333") == 1
    assert store.sent_count(day_start(), kinds=CHAT_KINDS) == 1
    assert store.action_count("proactive", day_start()) == 0
    # Even the next day, no chasing a person who never answered.
    store.set("friend_proactive_next:33333", 0)
    assert not workflow.eligible("33333", cfg, now + 86400 + 1)


@pytest.mark.asyncio
async def test_skip_is_durable_no_daily_minimum_and_no_cross_contact_context(env):
    workflow, store, cfg, engine, now, sent, calls = env
    store.incoming("private", "11111", "OWNER_PRIVATE")

    async def generate(system, contexts, **kwargs):
        calls.append((system, contexts))
        return '{"action":"skip","messages":[],"memory":[]}', None

    engine.generate = generate
    assert not await workflow.run(now)
    assert not sent and "OWNER_PRIVATE" not in str(calls)
    assert now + 90 * 60 <= store.get("friend_proactive_next:33333") <= now + 180 * 60
    assert not await workflow.run(now + 60)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_success_keeps_original_contact_interval(env):
    workflow, store, cfg, engine, now, sent, calls = env
    assert await workflow.run(now)
    assert store.get("friend_proactive_next:33333") == now + 1.5 * 3600


@pytest.mark.asyncio
async def test_sparse_friend_gets_priority_when_other_conditions_match(env, monkeypatch):
    workflow, store, cfg, engine, now, sent, calls = env
    store.set("known_friend_ids", ["33333", "44444"])
    for i in range(10):
        store.message("33333", "user", "聊过不少的话题", now - 4 * 3600)
    store.message("33333", "assistant", "好呀", now - 4 * 3600 + 1)
    store.incoming("sparse", "44444", "我也喜欢轻松的小画", now - 5 * 3600)
    store.message("44444", "assistant", "好呀", now - 5 * 3600 + 1)
    monkeypatch.setattr(friend_proactive.random, "shuffle", lambda candidates: None)
    assert await workflow.run(now)
    assert sent[0][0] == "44444"


@pytest.mark.asyncio
async def test_skip_rotates_assessment_before_reconsidering_same_friend(env):
    workflow, store, cfg, engine, now, sent, calls = env
    store.set("known_friend_ids", ["33333", "44444"])
    store.incoming("sparse", "44444", "喜欢饭团", now - 5 * 3600)
    store.message("44444", "assistant", "好呀", now - 5 * 3600 + 1)

    async def skip(*args, **kwargs):
        return dict(action="skip", messages=[])

    engine.decision = skip
    assert not await workflow.run(now)
    first = next(c for c in ["33333", "44444"] if store.get("friend_proactive_assessment:" + c))
    store.set("friend_proactive_next:" + first, 0)
    assert not await workflow.run(now + 60)
    assert all(store.get("friend_proactive_assessment:" + c) for c in ["33333", "44444"])
    assert not sent


@pytest.mark.asyncio
async def test_new_reply_can_reopen_analysis_before_skip_backoff_expires(env):
    workflow, store, cfg, engine, now, sent, calls = env
    store.set("friend_proactive_assessment:33333", dict(at=now, incoming=now - 5 * 3600))
    store.set("friend_proactive_next:33333", now + 3 * 3600)
    assert not workflow.eligible("33333", cfg, now + 3601)
    store.incoming("new", "33333", "刚想到一个想和你聊的东西", now + 1)
    store.message("33333", "assistant", "好呀", now + 2)
    assert workflow.eligible("33333", cfg, now + 3601)


@pytest.mark.parametrize(
    "case",
    [
        "owner",
        "stranger",
        "quiet",
        "unknown",
        "personal_cap",
        "global_cap",
        "recent",
        "disabled",
    ],
)
def test_ineligible_contacts_are_never_assessed(env, case):
    workflow, store, cfg, engine, now, sent, calls = env
    contact = "33333"
    if case == "owner":
        contact = "11111"
    if case == "stranger":
        contact = "44444"
    if case == "quiet":
        now = datetime.fromtimestamp(now, TZ).replace(hour=3).timestamp()
    if case == "unknown":
        store.action("uncertain", contact, "friend_proactive", ["unknown"])
        store.outcome("uncertain", "unknown")
    if case in {"personal_cap", "global_cap"}:
        store.action(
            "cap",
            contact if case == "personal_cap" else "11111",
            "reply",
            ["x"] * (20 if case == "personal_cap" else 200),
        )
        store.outcome("cap", "succeeded")
    if case == "recent":
        store.incoming("recent", contact, "hey", now - 1)
    if case == "disabled":
        cfg["proactive"] = False
    assert not workflow.eligible(contact, cfg, now)


@pytest.mark.asyncio
async def test_owner_global_cap_includes_friend_proactive_receipts(env):
    workflow, store, cfg, engine, now, sent, calls = env
    store.action("cap", "33333", "friend_proactive", ["x"] * 200)
    store.outcome("cap", "succeeded")
    store.incoming("owner", "11111", "hi")
    assert await engine.reply("11111")
    assert not calls and "200" in sent[0][1]


@pytest.mark.asyncio
async def test_contact_three_friends_then_respect_daily_unique_target(env):
    workflow, store, cfg, engine, now, sent, calls = env
    contacts = ["33333", "44444", "55555", "66666"]
    store.set("known_friend_ids", contacts)
    for contact in contacts[1:]:
        store.incoming("old-" + contact, contact, "聊过的日常", now - 5 * 3600)
        store.message(contact, "assistant", "好呀", now - 5 * 3600 + 1)
    # Three is a natural daily target, separate from per-person message caps.
    day = datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")
    store.set(f"friend_target:{cfg['bot_qq']}:{day}", 3)
    for _ in range(3):
        assert await workflow.run(now)
    assert len({contact for contact, _ in sent}) == 3
    assert not await workflow.run(now)
    assert len(calls) == 3


def test_previous_chat_is_required_and_busy_recipient_is_excluded(env):
    workflow, store, cfg, engine, now, sent, calls = env
    store.set("known_friend_ids", ["44444"])
    assert not workflow.eligible("44444", cfg, now)
    store.incoming("sleep", "44444", "我先去睡了", now - 3600)
    store.message("44444", "assistant", "晚安", now - 3599)
    engine.memory.observe("44444", now=now - 3600)
    assert not workflow.eligible("44444", cfg, now)


@pytest.mark.asyncio
async def test_three_for_same_friend_after_replies_and_explicit_two_still_wins(env):
    workflow, store, cfg, engine, now, sent, calls = env
    for index in range(3):
        store.incoming(f"answer-{index}", "33333", "继续聊", now - 3600)
        store.message("33333", "assistant", "好呀", now - 3599)
        # Simulate three naturally spaced opportunities on the same calendar day.
        store.db.execute("UPDATE actions SET created=created-5401 WHERE kind='friend_proactive'")
        store.set("friend_proactive_next:33333", 0)
        assert await workflow.run(now)
    assert len(sent) == 3
    store.incoming("fourth", "33333", "好", now - 3600)
    store.message("33333", "assistant", "嗯", now - 3599)
    store.set("friend_proactive_next:33333", 0)
    store.db.execute("UPDATE actions SET created=created-5401 WHERE kind='friend_proactive'")
    assert not workflow.eligible("33333", cfg, now)
    engine.memory.set_preference("44444", daily_cap=2)
    assert engine.personal_command("44444", "/主动频率").find("2 次") >= 0
