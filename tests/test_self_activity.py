import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from engine import Engine
from self_activity import own_post_context
from store import Store


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "facts.sqlite3")
    cfg = dict(
        owner_qq="11111",
        bot_qq="22222",
        name="fish",
        persona="kind",
        owner_title="主人",
        revision=1,
        live=True,
        paused=False,
        guests=True,
        guest_mode="open",
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
    )
    store.set("known_friend_ids", ["33333"])

    def post(bot, key, state, text):
        store.action(f"qzone_post:{bot}:{key}", f"qzone:{bot}", "qzone_post", [text])
        store.outcome(f"qzone_post:{bot}:{key}", state)

    post("22222", "today", "succeeded", "今天想给云朵起个名字")
    post("22222", "uncertain", "unknown", "UNCONFIRMED_POST")
    post("99999", "old-account", "succeeded", "FOREIGN_ACCOUNT_POST")
    store.incoming("other-friend", "44444", "OTHER_FRIEND_PRIVATE")
    yield store, cfg
    store.db.close()


def test_confirmed_own_posts_recalled_by_owner_and_verified_friends_only(env):
    store, cfg = env
    for contact in ["11111", "33333"]:
        text = own_post_context(store, cfg, contact)
        assert "今天想给云朵起个名字" in text
        assert "UNCONFIRMED" not in text and "FOREIGN_ACCOUNT" not in text
        assert "OTHER_FRIEND_PRIVATE" not in text
    assert own_post_context(store, cfg, "group:900000003") == ""
    assert own_post_context(store, cfg, "44444") == ""


@pytest.mark.asyncio
async def test_private_chat_receives_facts_without_cross_contact_history_and_public_draft_does_not(
    env,
):
    store, cfg = env
    prompts = []

    async def generate(system, contexts, **options):
        prompts.append((system, contexts))
        if options.get("response_mode") == "text":
            return "是我发的那条云朵", None
        return '{"action":"skip","messages":[],"memory":[]}', None

    engine = Engine(store, generate, None, lambda: cfg)
    store.incoming("friend", "33333", "你之前发了什么说说")
    assert await engine.decision("33333", "reply", "reply")
    assert "今天想给云朵起个名字" in prompts[-1][0]
    assert "OTHER_FRIEND_PRIVATE" not in str(prompts[-1])
    await engine.decision("11111", "qzone_post", "draft", public=True)
    assert "今天想给云朵起个名字" not in prompts[-1][0]


@pytest.mark.asyncio
async def test_repeated_interview_is_reviewed_but_real_questions_are_not(env):
    store, cfg = env
    store.incoming("first", "11111", "我喜欢一个干员")
    store.message("11111", "assistant", "你最喜欢哪个？")
    store.incoming("latest", "11111", "能天使，最喜欢了")
    calls = []

    async def generate(system, contexts, **options):
        calls.append((system, contexts))
        return "好嘛，本命位稳了", None

    engine = Engine(store, generate, None, lambda: cfg)
    result = dict(
        action="reply",
        messages=["她确实人气高，你现在最喜欢用她配什么队？"],
        revision=1,
        read_through=store.last_incoming("11111"),
    )
    revised = await engine.polish_reply("11111", result)
    assert revised["messages"] == ["好嘛，本命位稳了"] and len(calls) == 1
    assert "OTHER_FRIEND_PRIVATE" not in str(calls)
    store.incoming("question", "11111", "能天使怎么配队？")
    assert await engine.polish_reply("11111", result) == result
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_style_review_failure_keeps_deliverable_answer(env):
    store, cfg = env
    store.incoming("latest", "11111", "这是我最喜欢的角色")

    async def generate(*args, **kwargs):
        raise ConnectionError("offline")

    engine = Engine(store, generate, None, lambda: cfg)
    result = dict(action="reply", messages=["收到"], revision=1)
    assert await engine.polish_reply("11111", result) == result
