"""Resume audit: quiet-hour replies, private commands and concurrent outreach."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import engine as engine_module
from conversation_followups import ConversationFollowups
from digital_activity import DigitalActivities
from engine import Engine
from store import Store, TZ


class Clock(datetime):
    current = datetime(2026, 10, 21, 1, tzinfo=TZ)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


@pytest.fixture
def audit(tmp_path, monkeypatch):
    store = Store(tmp_path / "audit.sqlite3")
    cfg = dict(
        owner_qq="11111",
        bot_qq="22222",
        name="大肥鱼",
        persona="自然",
        owner_title="主人",
        revision=1,
        live=True,
        paused=False,
        guests=True,
        guest_mode="open",
        proactive=True,
        friend_proactive=True,
        conversation_followups=True,
        daily_calls=400,
        daily_cny=10,
        monthly_cny=100,
        daily_chat_messages=200,
        guest_daily_messages=20,
        proactive_min=1,
        proactive_max=5,
        friend_proactive_daily_max=2,
        digital_activities=True,
        memory_enabled=True,
    )
    calls, sent = [], []

    async def generate(system, contexts, **options):
        calls.append((system, contexts, options))
        if options.get("response_mode") == "text":
            return "我在呢，刚才说到海洋那个故事。", None
        return (
            '{"action":"reply","messages":["刚才那个故事有个新想法。"],"memory":[]}',
            None,
        )

    async def send(contact, text, **options):
        sent.append((contact, text))
        return {"message_id": len(sent)}

    engine = Engine(store, generate, send, lambda: dict(cfg))
    engine.activities = DigitalActivities(store, tmp_path)
    store.set("known_friend_ids", ["33333", "44444"])
    monkeypatch.setattr(engine_module, "datetime", Clock)
    monkeypatch.setattr("policy.datetime", Clock)
    monkeypatch.setattr("store.time.time", lambda: Clock.current.timestamp())
    yield SimpleNamespace(
        store=store,
        cfg=cfg,
        engine=engine,
        calls=calls,
        sent=sent,
        root=tmp_path,
        stamp=Clock.current.timestamp(),
    )
    store.db.close()


@pytest.mark.asyncio
async def test_passive_reply_still_delivers_after_midnight(audit):
    audit.store.incoming(
        "night-message", "33333", "还在吗，我们接着聊", now=audit.stamp
    )
    assert await audit.engine.reply("33333")
    assert len(audit.sent) == 1 and audit.sent[0][0] == "33333"
    assert len(audit.calls) == 1


@pytest.mark.asyncio
async def test_quiet_hours_defer_autonomous_activity_and_followups(audit):
    assert (
        await audit.engine.activities.run(audit.engine, audit.cfg, now=Clock.current)
        is None
    )
    plugin = SimpleNamespace(store=audit.store, engine=audit.engine, workers={})
    assert not await ConversationFollowups(plugin).run(audit.stamp)
    assert audit.calls == audit.sent == []
    assert not list(audit.root.glob("data/activities/**/*.json"))


@pytest.mark.asyncio
async def test_quiet_hours_block_unsolicited_delivery_but_not_direct_reply(audit):
    audit.store.incoming("night", "33333", "你好", now=audit.stamp)
    result = dict(
        action="reply", messages=["主动补充"], revision=1, read_through=audit.stamp
    )
    assert not await audit.engine.deliver(
        "33333", "friend_proactive", result, key="night-proactive"
    )
    assert await audit.engine.deliver("33333", "reply", result, key="night-passive")
    assert len(audit.sent) == 1


def test_personal_commands_cannot_select_or_modify_another_contact(audit):
    audit.store.remember("33333", "A_ONLY_SECRET")
    audit.store.remember("44444", "B_ONLY_SECRET")
    assert "A_ONLY_SECRET" in audit.engine.personal_command("33333", "/记忆")
    assert "B_ONLY_SECRET" not in audit.engine.personal_command("33333", "/记忆")
    assert "B_ONLY_SECRET" not in audit.engine.personal_command("11111", "/记忆")
    assert audit.engine.personal_command("33333", "/记忆 44444") is None
    assert audit.engine.personal_command("33333", "/日记 44444") is None
    previous = audit.engine.memory.preference("44444")
    audit.engine.personal_command("33333", "/主动频率 44444 关闭")
    assert audit.engine.memory.preference("44444") == previous
    audit.engine.personal_command("33333", "/主动频率 关闭")
    assert audit.engine.memory.preference("33333")["daily_cap"] == 0
    assert audit.engine.memory.preference("44444")["daily_cap"] == 2


@pytest.mark.asyncio
async def test_internal_memory_consolidation_at_night_has_no_qq_output(audit):
    audit.store.incoming("private-note", "33333", "我喜欢蓝鲸和海洋", now=audit.stamp)
    audit.store.message("33333", "assistant", "记下了。", now=audit.stamp + 1)

    async def extract(kind, system, contexts, **options):
        assert kind == "conversation_memory" and options["contact"] == "33333"
        payload = json.loads(contexts[0]["content"])
        row = next(row for row in payload["messages"] if row["role"] == "user")
        return json.dumps(
            {
                "facts": [
                    {
                        "key": "interest",
                        "source_id": row["source_id"],
                        "evidence": row["text"],
                    }
                ]
            },
            ensure_ascii=False,
        )

    audit.engine.generate_aux = extract
    assert await audit.engine.memory.consolidate(
        "33333", audit.engine, audit.cfg, force=True
    )
    assert audit.sent == []
    assert "我喜欢蓝鲸和海洋" in audit.engine.personal_command("33333", "/记忆")
    assert "我喜欢蓝鲸和海洋" not in audit.engine.personal_command("44444", "/记忆")


@pytest.mark.asyncio
async def test_two_concurrent_proactive_sources_do_not_chase_same_unanswered_turn(
    audit, monkeypatch
):
    monkeypatch.setattr(engine_module, "quiet", lambda moment: False)
    audit.store.incoming(
        "source", "33333", "刚才那个问题再说一点", now=audit.stamp - 600
    )
    marker = audit.store.last_incoming("33333")
    started, release = asyncio.Event(), asyncio.Event()

    async def send(contact, text, **options):
        audit.sent.append((contact, text))
        if len(audit.sent) == 1:
            started.set()
            await release.wait()
        return {"message_id": len(audit.sent)}

    audit.engine.send = send
    result = dict(
        action="reply", messages=["一个具体补充"], revision=1, read_through=marker
    )
    first = asyncio.create_task(
        audit.engine.deliver("33333", "friend_proactive", result, key="followup-first")
    )
    await started.wait()
    second = asyncio.create_task(
        audit.engine.deliver(
            "33333", "friend_proactive", result, key="ordinary-proactive-second"
        )
    )
    await asyncio.sleep(0)
    release.set()
    delivered = await asyncio.gather(first, second)
    assert delivered == [True, False]
    assert len(audit.sent) == 1


@pytest.mark.asyncio
async def test_owner_explicit_two_is_enforced_and_restore_uses_owner_default(
    audit, monkeypatch
):
    monkeypatch.setattr(engine_module, "quiet", lambda moment: False)
    answer = audit.engine.personal_command("11111", "/主动频率 2")
    assert "2 次" in answer
    sent = []
    for index in range(3):
        audit.store.incoming(f"owner-{index}", "11111", "接着上次那个话题聊吧")
        result = dict(
            action="reply",
            messages=["补充一个具体想法"],
            revision=1,
            read_through=audit.store.last_incoming("11111"),
        )
        sent.append(
            await audit.engine.deliver(
                "11111", "proactive", result, key=f"owner-active-{index}"
            )
        )
    assert sent == [True, True, False]
    restored = audit.engine.personal_command("11111", "/主动频率 恢复")
    assert "5 次" in restored
    assert audit.engine.memory.preference("11111")["explicit_cap"] is None
    assert len(audit.sent) == 2
