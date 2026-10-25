import json
import time
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from engine import DeliveryUnavailable, Engine, quiet
from settings import crypt_key
from store import TZ, BudgetExceeded, Store


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "test.sqlite3")
    cfg = dict(
        owner_qq="12345",
        bot_qq="67890",
        name="Test",
        persona="Kind",
        owner_title="主人",
        revision=1,
        live=True,
        paused=False,
        guests=True,
        proactive=True,
        daily_calls=150,
        daily_cny=3.0,
        monthly_cny=60.0,
    )
    sent, prompts = [], []

    async def generate(system, contexts, **options):
        prompts.append((system, contexts))
        return json.dumps(dict(action="reply", messages=["hello"], memory=[])), dict(
            input_other=100, input_cached=0, output=20
        )

    async def send(contact, text):
        sent.append((contact, text))
        return {"message_id": 123}

    engine = Engine(store, generate, send, lambda: dict(cfg))
    yield store, cfg, engine, sent, prompts
    store.db.close()


def test_event_dedup_and_contact_isolation(setup):
    store, *_ = setup
    assert store.incoming("a", "12345", "private owner fact")
    assert not store.incoming("a", "12345", "duplicate")
    store.incoming("b", "99999", "guest fact")
    assert len(store.history("12345")) == 1
    assert "guest" not in str(store.history("12345"))
    store.remember("12345", "secret")
    assert store.memories("99999") == []


@pytest.mark.asyncio
async def test_self_signature_cannot_be_treated_as_contact_profile(setup):
    store, cfg, engine, sent, prompts = setup
    store.set("self_profile:67890", dict(signature="喜欢和朋友有一句没一句地聊到心里去。", status="在线"))
    store.message("99999", "assistant", "你资料里那句有一句没一句地聊到心里去我很喜欢。")
    store.incoming("question", "99999", "我的资料里原来有这句吗")
    assert await engine.reply("99999")
    system = prompts[-1][0]
    assert "大肥鱼本人的公开QQ资料，不是当前聊天对象的资料" in system
    assert "应承认并纠正" in system and '"subject": "大肥鱼本人"' in system
    assert not store.memories("99999")


@pytest.mark.asyncio
async def test_actual_delivery_mode_overrides_old_voice_label_in_dialogue(setup):
    store, cfg, engine, sent, prompts = setup
    store.message("12345", "assistant", "[语音] 我这边是按语音发的。")
    store.set("last_delivery:12345", dict(mode="text", message_id="verified-text", at=time.time()))
    store.incoming("question", "12345", "你这是语音吗")
    assert await engine.reply("12345")
    assert '"mode": "text"' in prompts[-1][0]
    assert "mode=text就如实承认上一条是文字" in prompts[-1][0]
    assert store.get("last_delivery:12345")["mode"] == "text"
    store.forget("12345")
    assert store.get("last_delivery:12345") is None


@pytest.mark.asyncio
async def test_delayed_reply_gets_a_bridge_then_answers_original_message(setup):
    store, cfg, engine, sent, prompts = setup
    store.incoming("old", "12345", "后来那件事你怎么看")
    store.set("offline_context:12345", dict(source_time=time.time()-3600))
    assert await engine.reply("12345")
    system = prompts[-1][0]
    assert "缓和话，再继续认真回应原话" in system
    assert "不把这句缓和话记成已发生的生活事实" in system
    assert store.get("offline_context:12345") is None


def test_forget_removes_injected_history_and_notes(setup):
    store, *_ = setup
    store.incoming("a", "12345", "secret")
    store.remember("12345", "secret")
    store.forget("12345")
    assert store.history("12345") == [] and store.memories("12345") == []


def test_interrupted_reservation_is_released_after_restart_but_record_is_kept(tmp_path, setup):
    _, cfg, *_ = setup
    path = tmp_path / "budget.sqlite3"
    store = Store(path)
    cfg["daily_cny"] = 0.1
    rid = store.reserve(cfg, "reply", 0.08)
    store.db.close()
    store = Store(path)
    old = store.db.execute("SELECT state,amount FROM calls WHERE id=?", (rid,)).fetchone()
    assert tuple(old) == ("interrupted", .08)
    store.reserve(cfg, "reply", 0.08)
    with pytest.raises(BudgetExceeded):
        store.reserve(cfg, "reply", 0.08)
    store.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("auxiliary", [False, True])
async def test_thinking_budget_is_reserved_before_any_model_call(setup, auxiliary):
    store, cfg, engine, sent, prompts = setup
    cfg["daily_cny"] = 0.05
    store.incoming("thinking-budget", "12345", "A question")
    with pytest.raises(BudgetExceeded):
        if auxiliary:
            await engine.generate_aux("memory", "Summarize", [])
        else:
            await engine.decision("12345", "reply", "Answer")
    assert prompts == []
    assert sent == []


@pytest.mark.asyncio
async def test_disabled_and_self_never_generate(setup):
    store, cfg, engine, sent, prompts = setup
    cfg["live"] = False
    assert not await engine.reply("12345")
    cfg["live"] = True
    assert not await engine.reply("67890")
    assert sent == [] and prompts == []


@pytest.mark.asyncio
async def test_private_reply_and_memory_scope(setup):
    store, cfg, engine, sent, prompts = setup
    store.incoming("a", "12345", "owner private text")
    store.incoming("b", "99999", "guest text")
    store.remember("12345", "owner private memory")
    assert await engine.reply("99999")
    assert sent == [("99999", "hello")]
    assert "owner private" not in str(prompts)


@pytest.mark.asyncio
async def test_public_decision_never_includes_private_context(setup):
    store, cfg, engine, sent, prompts = setup
    store.incoming("a", "12345", "owner private text")
    store.remember("12345", "owner private memory")
    await engine.decision("12345", "qzone", "public post", public=True)
    assert "owner private" not in str(prompts)


@pytest.mark.asyncio
async def test_pause_during_generation_prevents_send(setup):
    store, cfg, engine, sent, prompts = setup

    async def generate(system, contexts, **options):
        cfg["paused"] = True
        cfg["revision"] += 1
        return '{"action":"reply","messages":["hello"]}', None

    engine.generate = generate
    assert not await engine.reply("12345")
    assert not sent


@pytest.mark.asyncio
async def test_new_message_invalidates_stale_reply(setup):
    store, cfg, engine, sent, prompts = setup
    store.incoming("a", "12345", "old")

    async def generate(system, contexts, **options):
        store.incoming("b", "12345", "new")
        return '{"action":"reply","messages":["stale"]}', None

    engine.generate = generate
    assert not await engine.reply("12345")
    assert not sent


@pytest.mark.asyncio
async def test_uncertain_delivery_blocks_automatic_retry(setup):
    store, cfg, engine, sent, prompts = setup

    async def fail(contact, text):
        raise TimeoutError()

    engine.send = fail
    with pytest.raises(TimeoutError):
        await engine.reply("12345")
    assert store.has_unknown("12345")
    n = len(prompts)
    assert not await engine.reply("12345")
    assert len(prompts) == n


@pytest.mark.asyncio
async def test_same_action_id_is_not_sent_twice(setup):
    store, cfg, engine, sent, prompts = setup
    result = {"action": "reply", "messages": ["hello"], "revision": 1}
    assert await engine.deliver("12345", "reply", result, key="fixed")
    assert not await engine.deliver("12345", "reply", result, key="fixed")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_proactive_waits_for_unanswered_owner(setup, monkeypatch):
    store, cfg, engine, sent, prompts = setup
    now = datetime.now(TZ).replace(hour=12, minute=0)
    monkeypatch.setattr("engine.quiet", lambda moment: False)
    monkeypatch.setattr("store.time.time", lambda: now.timestamp())
    store.incoming("a", "12345", "past topic", now.timestamp() - 3 * 3600)
    assert await engine.proactive(now)
    assert not await engine.proactive(now)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_proactive_quiet_hours_and_recent_contact(setup):
    store, cfg, engine, sent, prompts = setup
    now = datetime.now(TZ).replace(hour=12, minute=0)
    store.incoming("a", "12345", "recent", now.timestamp() - 60)
    assert not await engine.proactive(now)
    assert not await engine.proactive(now.replace(hour=0))
    assert not prompts


@pytest.mark.asyncio
async def test_invalid_model_output_does_not_send(setup):
    store, cfg, engine, sent, prompts = setup

    async def generate(system, contexts, **options):
        return "   ", None

    engine.generate = generate
    with pytest.raises(ValueError):
        await engine.reply("12345")
    assert not sent
    assert store.status()["calls_today"] == 2


def test_dpapi_round_trip_never_writes_plaintext():
    value = b"local-test-value-not-a-real-key"
    encrypted = crypt_key(value)
    assert value not in encrypted
    assert crypt_key(encrypted, decrypt=True) == value


def test_quiet_boundary():
    now = datetime.now(TZ)
    assert quiet(now.replace(hour=0))
    assert quiet(now.replace(hour=7))
    assert not quiet(now.replace(hour=8))


@pytest.mark.asyncio
async def test_decision_timing_has_phase_durations_without_private_content(setup, caplog):
    store, cfg, engine, sent, prompts = setup
    store.incoming("timed", "12345", "计时日志不能包含的私聊正文")
    with caplog.at_level("INFO", logger="qqcompanion"):
        assert await engine.reply("12345")
    entries = [r.getMessage() for r in caplog.records if r.getMessage().startswith("decision_timing ")]
    assert len(entries) == 1
    assert "kind=reply" in entries[0]
    assert all(key in entries[0] for key in ("context_ms=", "slot_wait_ms=", "generation_ms=", "total_ms="))
    assert "12345" not in entries[0]
    assert "计时日志不能包含的私聊正文" not in entries[0]


@pytest.mark.asyncio
async def test_decision_timing_includes_failed_model_attempt_before_retry(setup, caplog, monkeypatch):
    store, cfg, engine, sent, prompts = setup
    store.incoming("timed-retry", "12345", "一次失败后继续")
    clock = [100.0]
    attempts = []

    class EmptyModelOutputError(Exception):
        pass

    async def generate(*args, **kwargs):
        attempts.append(True)
        clock[0] += 0.5
        if len(attempts) == 1:
            raise EmptyModelOutputError("empty")
        return "这次有效。", None

    monkeypatch.setattr("engine.time.perf_counter", lambda: clock[0])
    engine.generate = generate
    with caplog.at_level("INFO", logger="qqcompanion"):
        assert await engine.reply("12345")
    entries = [r.getMessage() for r in caplog.records if r.getMessage().startswith("decision_timing ")]
    assert len(entries) == 1
    assert "generation_ms=1000.0" in entries[0]
    assert len(attempts) == 2


@pytest.mark.asyncio
async def test_plain_private_reply_never_requires_a_json_envelope(setup):
    store, cfg, engine, sent, prompts = setup

    async def generate(system, contexts, response_mode=None):
        assert response_mode == "text"
        assert "只输出 JSON" not in system
        return "主人，我在呢。", None

    engine.generate = generate
    assert await engine.reply("12345")
    assert sent == [("12345", "主人，我在呢。")]


@pytest.mark.asyncio
async def test_blank_output_retries_once_and_each_attempt_counts_against_budget(setup):
    store, cfg, engine, sent, prompts = setup
    calls = []

    async def generate(system, contexts, response_mode=None):
        calls.append(response_mode)
        return (" \n\t" if len(calls) == 1 else "现在可以正常回复了。"), None

    engine.generate = generate
    assert await engine.reply("12345")
    assert calls == ["text", "text"]
    assert len(sent) == 1
    assert store.status()["calls_today"] == 2


@pytest.mark.asyncio
async def test_second_blank_attempt_obeys_budget_instead_of_retrying_forever(setup):
    store, cfg, engine, sent, prompts = setup
    cfg["daily_calls"] = 1

    async def generate(system, contexts, response_mode=None):
        return " ", None

    engine.generate = generate
    with pytest.raises(BudgetExceeded):
        await engine.reply("12345")
    assert not sent
    assert store.status()["calls_today"] == 1


@pytest.mark.asyncio
async def test_sdk_empty_output_error_is_retried(setup):
    store, cfg, engine, sent, prompts = setup
    calls = []

    class EmptyModelOutputError(Exception):
        pass

    async def generate(system, contexts, response_mode=None):
        calls.append(True)
        if len(calls) == 1:
            raise EmptyModelOutputError("whitespace completion")
        return "这次回复有效。", None

    engine.generate = generate
    assert await engine.reply("12345")
    assert len(calls) == 2 and len(sent) == 1


@pytest.mark.asyncio
async def test_background_blank_falls_back_to_text_and_keeps_public_context_isolated(
    setup,
):
    store, cfg, engine, sent, prompts = setup
    store.incoming("event", "12345", "owner private text")
    modes = []

    async def generate(system, contexts, response_mode=None):
        assert "owner private text" not in str(contexts)
        modes.append(response_mode)
        return (
            " "
            if len(modes) == 1
            else '{"action":"reply","messages":["公开评论"],"memory":[]}'
        ), None

    engine.generate = generate
    result = await engine.decision(
        "12345", "qzone_comment", "a public post", public=True
    )
    assert result["messages"] == ["公开评论"]
    assert modes == [None, "text"]


@pytest.mark.asyncio
async def test_two_contacts_can_generate_concurrently(setup):
    store, cfg, engine, sent, prompts = setup
    entered = []
    both = asyncio.Event()

    async def generate(system, contexts, response_mode=None):
        entered.append(True)
        if len(entered) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), 2)
        return "你好。", None

    engine.generate = generate
    results = await asyncio.wait_for(
        asyncio.gather(engine.reply("12345"), engine.reply("99999")), 3
    )
    assert results == [True, True]
    assert {contact for contact, text in sent} == {"12345", "99999"}


@pytest.mark.asyncio
async def test_disconnection_before_send_can_recover_without_unknown_block(setup):
    store, cfg, engine, sent, prompts = setup

    async def unavailable(contact, text):
        raise DeliveryUnavailable("disconnected before send")

    original = engine.send
    engine.send = unavailable
    with pytest.raises(DeliveryUnavailable):
        await engine.reply("12345")
    assert not store.has_unknown("12345")
    engine.send = original
    assert await engine.reply("12345")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_persisted_queue_cannot_duplicate_an_already_delivered_reply(setup):
    store, cfg, engine, sent, prompts = setup
    store.incoming("event", "12345", "hello")
    assert await engine.reply("12345")
    # A crash can happen after a send receipt but before queue acknowledgement.
    assert not await engine.reply("12345")
    assert len(sent) == 1
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_proactive_snooze_does_not_disable_private_reply(setup):
    store, cfg, engine, sent, prompts = setup
    now = datetime.now(TZ).replace(hour=12, minute=0)
    store.incoming("a", "12345", "past topic", now.timestamp() - 3 * 3600)
    store.set("proactive_snooze_until", now.timestamp() + 7200)
    assert not await engine.proactive(now)
    assert await engine.reply("12345")
    assert len(sent) == 1
