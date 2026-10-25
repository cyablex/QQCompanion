"""Exercise optimized chat through real Engine/Store boundaries, no network calls."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import engine as engine_module
import digital_activity as activity_module
import friend_proactive as friend_module
import store as store_module
from engine import Engine
from friend_proactive import FriendProactive
from group_chat import GroupEngine
from policy import day_start
from store import CHAT_KINDS, Store, TZ


@pytest.fixture
def optimized(tmp_path):
    store = Store(tmp_path / "integration.sqlite3")
    cfg = dict(
        owner_qq="11111",
        bot_qq="22222",
        name="大肥鱼",
        persona="完整测试人设",
        owner_title="主人",
        revision=1,
        live=True,
        paused=False,
        guests=True,
        guest_mode="open",
        proactive=True,
        friend_proactive=True,
        friend_proactive_daily_max=2,
        friend_proactive_interval_hours=6,
        daily_calls=400,
        daily_cny=5,
        monthly_cny=60,
        daily_chat_messages=200,
        guest_daily_messages=20,
        memory_enabled=True,
        digital_activities=False,
        group_enabled=True,
        group_ids=["99999"],
    )
    calls, sent = [], []

    async def generate(system, contexts, **kwargs):
        calls.append((system, contexts, kwargs))
        if kwargs.get("response_mode") == "text":
            return "好，我记着这件事。", None
        return json.dumps(
            {
                "action": "reply",
                "messages": ["说到刚才那个海洋故事，我想到一个结尾。"],
                "memory": [],
            },
            ensure_ascii=False,
        ), None

    async def send(contact, text, **kwargs):
        sent.append((contact, text, kwargs))
        return {"message_id": len(sent)}

    engine = Engine(store, generate, send, lambda: dict(cfg))
    yield SimpleNamespace(
        store=store, cfg=cfg, engine=engine, calls=calls, sent=sent, root=tmp_path
    )
    store.db.close()


class ExtractFacts:
    async def generate_aux(self, kind, system, contexts, **kwargs):
        rows = json.loads(contexts[0]["content"])["messages"]
        return json.dumps(
            {
                "facts": [
                    {
                        "key": "private_interest",
                        "source_id": row["source_id"],
                        "evidence": row["text"],
                    }
                    for row in rows
                    if row["role"] == "user"
                ]
            },
            ensure_ascii=False,
        )


@pytest.mark.asyncio
async def test_engine_scopes_legacy_memory_retrieved_facts_history_and_group(optimized):
    env = optimized
    a, b = "33333", "44444"
    fact_a, fact_b = (
        "我有一个只告诉你的爱好：收集蓝色贝壳",
        "我有一个只告诉你的计划：练习陶笛",
    )
    env.store.incoming("a", a, fact_a)
    env.store.incoming("b", b, fact_b)
    env.store.remember(a, "A_ONLY_MANUAL_SECRET")
    env.store.remember(b, "B_ONLY_MANUAL_SECRET")
    await env.engine.memory.consolidate(a, ExtractFacts(), env.cfg, force=True)
    await env.engine.memory.consolidate(b, ExtractFacts(), env.cfg, force=True)
    env.store.incoming("g", "group:99999", "GROUP_ONLY_TOPIC")

    await env.engine.decision(a, "reply", "继续聊")
    own = str(env.calls[-1])
    assert fact_a in own and "A_ONLY_MANUAL_SECRET" in own
    assert fact_b not in own and "B_ONLY_MANUAL_SECRET" not in own
    await env.engine.decision(b, "reply", "继续聊")
    other = str(env.calls[-1])
    assert (
        fact_b in other and fact_a not in other and "A_ONLY_MANUAL_SECRET" not in other
    )

    await env.engine.decision(a, "space_comment", "PUBLIC_POST_TOPIC", public=True)
    public = str(env.calls[-1])
    assert "PUBLIC_POST_TOPIC" in public
    for secret in (fact_a, fact_b, "A_ONLY_MANUAL_SECRET", "B_ONLY_MANUAL_SECRET"):
        assert secret not in public
    group = GroupEngine(
        env.store, env.engine.generate, env.engine.send, lambda: dict(env.cfg)
    )
    await group.decision("group:99999", "reply", "接群里的话")
    group_prompt = str(env.calls[-1])
    assert "GROUP_ONLY_TOPIC" in group_prompt
    for secret in (fact_a, fact_b, "A_ONLY_MANUAL_SECRET", "B_ONLY_MANUAL_SECRET"):
        assert secret not in group_prompt


@pytest.mark.asyncio
async def test_latest_question_drives_old_original_recall_after_a_long_prior_topic(
    optimized, monkeypatch
):
    env = optimized
    queries = []
    actual_context = env.engine.memory.context

    def capture(contact, query="", now=None):
        queries.append(query)
        return actual_context(contact, query, now)

    monkeypatch.setattr(env.engine.memory, "context", capture)
    env.store.incoming("very-old", "33333", "我给徽章取名叫小海马泡泡")
    for index in range(34):
        env.store.incoming(
            f"filler-{index}",
            "33333",
            "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏润余成岁律吕调阳云腾致雨露结为霜"
            + str(index),
        )
    env.store.incoming("current-question", "33333", "徽章叫什么名字你还记得吗？")
    assert "小海马泡泡" not in str(env.store.history("33333"))
    await env.engine.decision("33333", "reply", "回应当前提问")
    assert queries == ["徽章叫什么名字你还记得吗？"]
    assert "小海马泡泡" in env.calls[-1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "old_turns,question,evidence",
    [
        (
            [
                ("user", "我喝咖啡只选燕麦奶，牛奶喝了不舒服。"),
                ("assistant", "好，下次点咖啡记得选燕麦奶。"),
            ],
            "我买咖啡选什么奶来着？",
            ["我喝咖啡只选燕麦奶，牛奶喝了不舒服。"],
        ),
        (
            [
                ("user", "项目演示最初定在周三下午。"),
                ("assistant", "那是周三下午。"),
                ("user", "老师通知改成周五上午了，周三的不算了。"),
            ],
            "我演示到底哪天来着？",
            ["项目演示最初定在周三下午。", "老师通知改成周五上午了，周三的不算了。"],
        ),
    ],
)
async def test_prompt_contains_real_old_preference_and_its_following_correction(
    optimized, old_turns, question, evidence
):
    env = optimized
    for role, text in old_turns:
        env.store.message("33333", role, text)
    for index in range(36):
        env.store.message(
            "33333",
            "user" if index % 2 == 0 else "assistant",
            f"换个话题随便聊点，第{index}条。" if index % 2 == 0 else "好，接着聊。",
        )
    env.store.incoming("recall-question", "33333", question)
    assert not any(text in str(env.store.history("33333")) for text in evidence)
    await env.engine.decision("33333", "reply", "回复最近的消息")
    system = env.calls[-1][0]
    for text in evidence:
        assert text in system


@pytest.mark.asyncio
async def test_real_activity_flows_to_chat_and_public_without_reading_private_memory(
    optimized, monkeypatch
):
    env = optimized
    env.cfg["digital_activities"] = True
    env.engine.activities.root = env.root
    env.store.set("known_friend_ids", ["33333"])
    env.store.remember("11111", "OWNER_ONLY_PRIVATE")
    env.store.remember("33333", "FRIEND_ONLY_PRIVATE")
    source_excerpt = "ACTUAL_PUBLIC_SOURCE_EXCERPT"
    note = "独立阅读蓝鲸资料后，我想继续了解它们在远距离如何交流。这是只依据公开节选保存的一份阅读笔记。"

    async def fetch(*args):
        return dict(
            title="蓝鲸资料", url="https://www.fisheries.noaa.gov/species/blue-whale", category="nature",
            text=source_excerpt,
            scope="article_excerpt",
            retrieved_at=day_start() + 12 * 3600,
            truncated=True,
            sha256="fixture",
        )

    async def generate(system, contexts, **kwargs):
        env.calls.append((system, contexts, kwargs))
        if "为今天选一次" in system:
            return json.dumps(dict(action="read", category="nature", query="蓝鲸")), None
        if "一次独立数字活动" in system:
            return json.dumps(
                {
                    "action": "complete",
                    "title": "蓝鲸阅读笔记",
                    "content": note,
                    "next_step": "写一段明确虚构的海洋故事",
                },
                ensure_ascii=False,
            ), None
        if kwargs.get("response_mode") == "text":
            return "今天写了一份蓝鲸阅读笔记。", None
        return '{"action":"skip","messages":[],"memory":[]}', None

    monkeypatch.setattr(activity_module, "discover_article", fetch)
    env.engine.generate = generate
    now = datetime.now(TZ).replace(hour=14, minute=0, second=0, microsecond=0)
    record = await env.engine.activities.run(env.engine, env.cfg, now=now)
    assert record["status"] == "completed"
    assert Path(record["artifact"]).is_file()
    activity_prompt = str(env.calls[1])
    assert source_excerpt in activity_prompt
    assert (
        "OWNER_ONLY_PRIVATE" not in activity_prompt
        and "FRIEND_ONLY_PRIVATE" not in activity_prompt
    )
    await env.engine.decision("33333", "reply", "最近在做什么")
    friend_prompt = str(env.calls[-1])
    assert note in friend_prompt and "FRIEND_ONLY_PRIVATE" in friend_prompt
    assert "OWNER_ONLY_PRIVATE" not in friend_prompt
    await env.engine.decision("44444", "reply", "最近在做什么")
    assert note not in str(env.calls[-1])
    await env.engine.decision("33333", "space_comment", "公开聊蓝鲸", public=True)
    public_prompt = str(env.calls[-1])
    assert note in public_prompt
    assert (
        "OWNER_ONLY_PRIVATE" not in public_prompt
        and "FRIEND_ONLY_PRIVATE" not in public_prompt
    )
    assert not env.sent  # Independent activity has no automatic send side effect.


@pytest.mark.asyncio
async def test_forget_during_ordinary_reply_never_sends_or_rewrites_deleted_chat(
    optimized,
):
    env = optimized
    env.store.incoming("private", "33333", "请只记在我们之间的秘密")
    env.store.remember("33333", "需要清除的秘密")
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        await release.wait()
        return "还记得你那个秘密。", None

    env.engine.generate = slow
    pending = asyncio.create_task(env.engine.reply("33333"))
    await started.wait()
    env.store.forget("33333")
    release.set()
    assert not await pending
    assert env.sent == []
    assert env.store.history("33333") == []
    assert env.store.memories("33333") == []
    assert (
        env.store.db.execute(
            "SELECT COUNT(*) FROM cm_items WHERE contact='33333'"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.asyncio
async def test_forget_during_generation_invalidates_even_zero_incoming_marker(
    optimized,
):
    """Manual memory can exist before the first retained user message."""
    env = optimized
    env.store.remember("33333", "尚无历史消息但已经存在的秘密")
    assert env.store.last_incoming("33333") == 0
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(*args, **kwargs):
        started.set()
        await release.wait()
        return "尚无历史消息但已经存在的秘密", None

    env.engine.generate = slow
    pending = asyncio.create_task(env.engine.decision("33333", "reply", "继续聊"))
    await started.wait()
    env.store.forget("33333")
    release.set()
    result = await pending
    assert not await env.engine.deliver("33333", "reply", result)
    assert env.sent == [] and env.store.history("33333") == []


@pytest.mark.asyncio
async def test_normal_reply_uses_one_generation_even_when_old_polish_would_trigger(
    optimized,
):
    env = optimized
    env.store.message("33333", "assistant", "你最喜欢谁？")
    env.store.incoming("share", "33333", "我最喜欢的角色是小蓝鲸")
    assert "chat_style_review" not in env.cfg
    assert await env.engine.reply("33333")
    assert len(env.calls) == 1
    assert len(env.sent) == 1
    assert env.store.db.execute("SELECT kind FROM calls").fetchall()[0][0] == "reply"


@pytest.mark.asyncio
async def test_engine_passes_full_overlong_response_to_safe_semantic_limiter(optimized):
    env = optimized
    env.store.incoming("long-code", "33333", "这段代码怎么写？")

    async def generate(*args, **kwargs):
        return "```text\n" + "鱼" * 1500 + "\n```", None

    env.engine.generate = generate
    result = await env.engine.decision("33333", "reply", "继续解释")
    assert len(result["messages"]) == 1
    assert len(result["messages"][0]) <= 1200
    assert result["messages"][0].endswith("\n```")


@pytest.mark.asyncio
async def test_chat_bubbles_charge_each_quota_and_attach_sticker_only_at_close(
    optimized,
):
    env = optimized
    env.cfg["send_memes"] = True
    env.store.incoming("split", "33333", "这故事你觉得哪里能改？")

    class Stickers:
        items = {"085_wink": {}}

        def prompt(self, owner=False):
            return "可选表情。"

        def extract(self, text):
            return text.removesuffix("\n[表情:085_wink]"), "085_wink"

    async def generate(*args, **kwargs):
        return (
            "我喜欢这个开头。\n\n结尾可以留一点悬念。\n\n先别急着把谜底说完。\n[表情:085_wink]",
            None,
        )

    env.engine.stickers, env.engine.generate = Stickers(), generate
    assert await env.engine.reply("33333")
    assert len(env.sent) == 3
    assert env.sent[0][2] == {} and env.sent[1][2] == {}
    assert env.sent[-1][2] == {"sticker_id": "085_wink"}
    assert env.store.sent_count(day_start(), "33333") == 3
    assert env.store.sent_count(day_start(), kinds=CHAT_KINDS) == 3
    assert env.store.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 3


@pytest.mark.asyncio
async def test_split_cannot_overrun_remaining_contact_quota(optimized):
    env = optimized
    env.cfg["guest_daily_messages"] = 2
    env.store.incoming("cap", "33333", "说说你的看法")
    result = dict(
        action="reply",
        messages=["第一点", "第二点", "第三点"],
        revision=1,
        read_through=env.store.last_incoming("33333"),
    )
    assert await env.engine.deliver("33333", "reply", result)
    assert [row[1] for row in env.sent] == ["第一点", "第二点"]
    assert env.store.sent_count(day_start(), "33333") == 2


@pytest.mark.asyncio
async def test_friend_two_in_day_is_reachable_after_replies_but_third_denied(
    optimized, monkeypatch
):
    env = optimized
    day = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    clock = [day.timestamp() + 3 * 3600]
    monkeypatch.setattr(store_module.time, "time", lambda: clock[0])
    monkeypatch.setattr(friend_module, "read_settings", lambda: dict(env.cfg))
    monkeypatch.setattr(friend_module.random, "randint", lambda low, high: low)
    monkeypatch.setattr(engine_module, "quiet", lambda moment: False)
    env.store.set("known_friend_ids", ["33333"])
    workflow = FriendProactive(
        SimpleNamespace(store=env.store, engine=env.engine, workers={})
    )

    def exchange(index, when):
        clock[0] = when
        env.store.incoming(
            f"answer-{index}", "33333", "这个海洋故事明天还想继续聊", now=when
        )
        clock[0] += 60
        env.store.action(f"answer-reply-{index}", "33333", "reply", ["好呀"])
        env.store.outcome(f"answer-reply-{index}", "succeeded")
        env.store.message("33333", "assistant", "好呀")

    exchange(0, day.timestamp() + 3 * 3600)
    clock[0] = day.timestamp() + 8 * 3600
    assert await workflow.run(clock[0])
    assert not workflow.eligible("33333", env.cfg, clock[0] + 7 * 3600)  # unanswered
    exchange(1, day.timestamp() + 8.5 * 3600)
    clock[0] = day.timestamp() + 14 * 3600
    assert await workflow.run(clock[0])
    exchange(2, day.timestamp() + 14.5 * 3600)
    clock[0] = day.timestamp() + 20 * 3600
    assert not workflow.eligible("33333", env.cfg, clock[0])
    assert not await workflow.run(clock[0])
    assert len(env.sent) == 2
    assert env.store.action_count("friend_proactive", day.timestamp(), "33333") == 2
    keys = [
        row[0]
        for row in env.store.db.execute(
            "SELECT id FROM actions WHERE kind='friend_proactive' ORDER BY created"
        )
    ]
    assert keys[0].endswith(":1") and keys[1].endswith(":2")


@pytest.mark.asyncio
async def test_newly_applied_contact_preference_denies_previously_generated_proactive(
    optimized, monkeypatch
):
    env = optimized
    monkeypatch.setattr(engine_module, "quiet", lambda moment: False)
    env.store.incoming("preference", "33333", "以后不要主动联系我")
    result = await env.engine.decision("33333", "friend_proactive", "有合适话题才发")
    marker = env.store.last_incoming("33333")
    # Parsing can finish after generation: only policy changes here, not freshness.
    env.engine.memory.observe("33333")
    assert env.store.last_incoming("33333") == marker
    assert env.engine.memory.preference("33333")["daily_cap"] == 0
    assert not await env.engine.deliver("33333", "friend_proactive", result)
    assert not env.sent
