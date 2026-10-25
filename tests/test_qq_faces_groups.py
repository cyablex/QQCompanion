import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import group_chat
from engine import Engine
from group_chat import GroupChats
from media import MediaPipeline
from qq_faces import FACE_NAMES, FACE_IDS, segments
from store import Store


def test_native_faces_receive_and_send_without_leaking_markers():
    assert len(FACE_NAMES) >= 300
    text, attachments = MediaPipeline.describe_input(
        {
            "message": [
                {"type": "face", "data": {"id": 264}},
                {"type": "face", "data": {"id": 182}},
            ]
        },
        "hello",
    )
    assert "捂脸" in text and "笑哭" in text and not attachments
    result = segments("哈哈[QQ表情:笑哭]懂了[QQ表情:捂脸]")
    assert sum(x["type"] == "face" for x in result) == 1
    assert result[1] == {"type": "face", "data": {"id": FACE_IDS["笑哭"]}}
    assert "QQ表情" not in str(result)
    assert not any(x["type"] == "face" for x in segments("嗯[QQ表情:笑哭]", False))
    assert not any(x["type"] == "face" for x in segments("[QQ表情:伪造名称]"))


@pytest.fixture
def groups(tmp_path, monkeypatch):
    cfg = dict(
        live=True,
        paused=False,
        owner_qq="11111",
        bot_qq="22222",
        name="fish",
        persona="kind",
        owner_title="主人",
        revision=1,
        guests=True,
        group_ids=["900000003"],
        group_enabled=True,
        group_interjections=True,
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
        daily_chat_messages=200,
        guest_daily_messages=20,
        send_memes=True,
    )
    store = Store(tmp_path / "groups.sqlite3")
    calls, sent = [], []

    async def generate(system, contexts, **options):
        calls.append((system, contexts))
        return "group answer" if options.get(
            "response_mode"
        ) == "text" else '{"action":"skip","messages":[],"memory":[]}', None

    async def send(*args, **kwargs):
        sent.append((args, kwargs))
        return {"message_id": len(sent)}

    async def action(name, **kwargs):
        sent.append((name, kwargs))
        return {"message_id": len(sent)}

    async def check():
        pass

    engine = Engine(store, generate, send, lambda: cfg)
    plugin = SimpleNamespace(
        store=store,
        engine=engine,
        generate=generate,
        check_account=check,
        bot=SimpleNamespace(call_action=action),
    )
    chats = GroupChats(plugin)
    chats.engine.config_reader = lambda: cfg
    monkeypatch.setattr(group_chat, "read_settings", lambda: cfg)
    yield chats, store, cfg, calls, sent
    store.db.close()


@pytest.mark.asyncio
async def test_group_context_never_includes_private_or_other_group_history(groups):
    chats, store, cfg, calls, sent = groups
    store.incoming("private", "11111", "PRIVATE_SECRET")
    store.remember("11111", "PRIVATE_MEMORY")
    store.incoming("other", "group:99999", "OTHER_GROUP_SECRET")
    store.incoming("group", "group:900000003", "[群成员 A] hello")
    assert await chats.engine.reply("group:900000003")
    assert len(sent) == 1 and sent[0][0] == "send_group_msg"
    assert sent[0][1]["group_id"] == 900000003
    assert "PRIVATE" not in str(calls) and "OTHER_GROUP" not in str(calls)
    assert store.sent_count(group_chat.day_start(), "group:900000003") == 1
    assert not chats.engine.allowed(cfg, "group:99999")
    assert not chats.engine.allowed(cfg, "11111")


@pytest.mark.asyncio
async def test_mention_required_unless_interjection_thresholds_met(groups):
    chats, store, cfg, calls, sent = groups
    # Exercise the actual event handler. Replace only its sleeping worker.
    handled = []

    async def drain(contact):
        handled.append(contact)

    chats.drain = drain
    raw = dict(
        group_id=99999,
        user_id=33333,
        message_id=1,
        message=[dict(type="text", data={"text": "hello"})],
    )
    await chats.receive(raw)
    assert not store.history("group:99999") and not chats.tasks
    raw.update(group_id=900000003)
    raw["message"].append(dict(type="at", data={"qq": "22222"}))
    await chats.receive(raw)
    await asyncio.gather(*chats.tasks.values())
    assert handled == ["group:900000003"] and chats.pending["group:900000003"]
    await chats.interject("group:900000003")
    assert not calls and not sent


@pytest.mark.asyncio
async def test_interjection_may_skip_and_daily_cap_prevents_generation(
    groups, monkeypatch
):
    chats, store, cfg, calls, sent = groups
    contact = "group:900000003"
    monkeypatch.setattr(group_chat, "quiet", lambda now: False)
    for n in range(8):
        store.incoming(f"event{n}", contact, "[群成员 A] fish topic")
    store.set("group_last_reply:" + contact, time.time() - 7200)
    store.set("group_since_reply:" + contact, 10)
    await chats.interject(contact)
    assert len(calls) == 1 and not sent
    store.set("group_last_assessment:" + contact, 0)
    for n in range(2):
        key = f"group_interjection:{contact}:{n}"
        store.action(key, contact, "reply", ["old"])
        store.outcome(key, "succeeded")
    await chats.interject(contact)
    assert len(calls) == 1 and not sent


@pytest.mark.asyncio
async def test_group_recent_topic_excludes_yesterdays_questions_even_when_mentioned(groups):
    chats, store, cfg, calls, sent = groups
    contact = "group:900000003"
    yesterday = time.time() - 86400
    store.incoming("old-homework", contact, "[群成员 A] 我可以让她帮我写作业吗", now=yesterday)
    store.incoming("old-plan", contact, "[群成员 B] 快进到明晚", now=yesterday + 1)
    store.incoming("current", contact, "[群成员 C] 串台了吗")
    assert await chats.engine.reply(contact)
    system, contexts = calls[-1]
    assert "写作业" not in str(contexts) and "明晚" not in str(contexts)
    assert "串台了吗" in str(contexts) and "北京时间" in str(contexts)
    assert "这是多人群聊" in system and "这是普通私聊" not in system


@pytest.mark.asyncio
async def test_old_counter_and_unread_attachment_do_not_trigger_interjection(groups, monkeypatch):
    chats, store, cfg, calls, sent = groups
    contact = "group:900000003"
    monkeypatch.setattr(group_chat, "quiet", lambda now: False)
    for n in range(10):
        store.incoming(f"old{n}", contact, "[群成员 A] 我可以让她帮我写作业吗", now=time.time() - 86400 + n)
    store.incoming("picture", contact, "[群成员 B] [群聊附件暂未解析，不猜测附件内容]")
    store.set("group_last_reply:" + contact, time.time() - 7200)
    store.set("group_since_reply:" + contact, 11)
    await chats.interject(contact)
    assert not calls and not sent
    # Even a burst of faces and unread images does not invent a current topic.
    for n in range(8):
        store.incoming(f"face{n}", contact, "[群成员 B] [QQ表情：对的对的]")
    await chats.interject(contact)
    assert not calls and not sent
