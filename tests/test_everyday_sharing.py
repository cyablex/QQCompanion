import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import autopost
from engine import Engine
from sketchbook import Sketchbook
from store import Store


def picture():
    return dict(action="draw", title="晒太阳的小鱼", caption="给自己画了个暖和的位置。", mood="开心",
                composition="一只放松的小鱼躺在暖阳下，没有科普目的。", background="#fff7dd", elements=[
                    dict(type="ellipse", box=[650, 80, 850, 280], fill="#ffdd66"),
                    dict(type="rect", box=[0, 700, 1000, 1000], fill="#aadd99"),
                    dict(type="ellipse", box=[200, 550, 600, 750], fill="#bbddff")])


@pytest.fixture
def env(tmp_path):
    cfg = dict(owner_qq="11111", bot_qq="22222", name="大肥鱼", persona="喜欢海洋和白米饭，开朗调皮",
               owner_title="主人", live=True, paused=False, guests=True, guest_mode="open", revision=1,
               proactive=True, friend_proactive=True, autonomous_drawing=True, digital_activities=True,
               daily_calls=1000, daily_cny=3, monthly_cny=60)
    store = Store(tmp_path / "everyday.sqlite3")
    store.set("known_friend_ids", ["33333", "44444"])
    prompts, sent = [], []
    response = dict(action="reply", messages=["画里的两只小鱼，让我想到和你聊天时的开心。"],
                    drawing_brief="两条小鲸鱼在暖暖的阳光里开心地摆尾，轻松舒服的画面，不需要科普寓意。")

    async def generate(system, contexts, **kwargs):
        prompts.append((system, contexts))
        return json.dumps(picture() if "几何图形自由构图" in system else response, ensure_ascii=False), None

    async def send(contact, text, **kwargs):
        sent.append((contact, text, kwargs))
        return dict(message_id=len(sent))

    engine = Engine(store, generate, send, lambda: dict(cfg))
    engine.sketchbook = Sketchbook(store, tmp_path)
    yield SimpleNamespace(store=store, cfg=cfg, engine=engine, prompts=prompts, sent=sent, response=response)
    store.db.close()


def seed_reading(env):
    env.engine.activities.library.save(env.cfg, dict(id="public-whale", day="2026-10-22", kind="reading_note",
        status="completed", public_safe=True, origin="independent_public_activity", title="鲸鱼方言地图",
        content="WHALE_RESEARCH_PROGRESS，座头鲸捕食方式有地方差异。", next_step="NOT_DONE_RESEARCH_PLAN"))


@pytest.mark.asyncio
async def test_unrelated_private_chat_does_not_receive_recent_research_material(env):
    seed_reading(env)
    env.store.incoming("daily", "33333", "今天的作业终于写完了，脑子转不动啦")
    await env.engine.decision("33333", "reply", "回复当前消息")
    assert "WHALE_RESEARCH_PROGRESS" not in str(env.prompts)
    assert "NOT_DONE_RESEARCH_PLAN" not in str(env.prompts)


@pytest.mark.asyncio
async def test_outreach_retrieves_only_recipient_relevant_completed_material_not_plans(env):
    seed_reading(env)
    env.store.incoming("interest", "33333", "我也挺喜欢研究鲸鱼方言地图的")
    await env.engine.decision("33333", "friend_proactive", "只有适合当前好友才分享")
    assert "WHALE_RESEARCH_PROGRESS" in str(env.prompts)
    assert "NOT_DONE_RESEARCH_PLAN" not in str(env.prompts)


@pytest.mark.asyncio
async def test_spontaneous_friend_drawing_is_real_private_and_sent_to_one_contact(env, monkeypatch):
    # Test image delivery independently of the machine's current quiet hours.
    monkeypatch.setattr("engine.quiet", lambda now: False)
    env.store.incoming("warm", "33333", "和你瞎聊挺开心的，我喜欢你画的小东西")
    result = await env.engine.decision("33333", "friend_proactive", "这次想画两个开心伙伴，像你们聊天")
    result = await env.engine.prepare_proactive_drawing("33333", result, env.cfg)
    assert result and Path(result["drawing_file"]).is_file()
    assert env.engine.sketchbook.allowed_file(env.cfg, result["drawing_file"], "33333")
    assert not env.engine.sketchbook.allowed_file(env.cfg, result["drawing_file"], "44444")
    assert await env.engine.deliver("33333", "friend_proactive", result)
    assert env.sent[0][0] == "33333" and env.sent[0][2]["image_file"] == result["drawing_file"]
    assert env.engine.sketchbook.records(env.cfg) == []
    assert "晒太阳的小鱼" not in env.engine.sketchbook.context("44444", env.cfg)
    assert not env.store.get("own_post_requested")


@pytest.mark.asyncio
async def test_spontaneous_drawing_skip_or_failure_does_not_send_fake_picture(env, monkeypatch):
    async def skip(*args, **kwargs):
        return None

    monkeypatch.setattr(env.engine.sketchbook, "create", skip)
    result = dict(action="reply", messages=["画好了给你看"], drawing_brief="晒太阳的小鱼")
    result = await env.engine.prepare_proactive_drawing("33333", result, env.cfg)
    assert result is None
    assert not await env.engine.deliver("33333", "friend_proactive", result)
    assert not env.sent


@pytest.mark.asyncio
async def test_new_incoming_after_drawing_cancels_stale_outreach(env):
    env.store.incoming("first", "33333", "聊天好开心")
    result = await env.engine.decision("33333", "friend_proactive", "画两个开心伙伴")
    result = await env.engine.prepare_proactive_drawing("33333", result, env.cfg)
    env.store.incoming("new", "33333", "我先忙了")
    assert not await env.engine.deliver("33333", "friend_proactive", result)
    assert not env.sent


@pytest.mark.asyncio
async def test_spontaneous_drawing_has_one_attempt_per_contact_per_day(env):
    result = dict(action="reply", messages=["小画"], drawing_brief="晒太阳")
    assert await env.engine.prepare_proactive_drawing("33333", result, env.cfg)
    assert await env.engine.prepare_proactive_drawing("33333", result, env.cfg) is None
    assert len([p for p in env.prompts if "几何图形自由构图" in p[0]]) == 1


@pytest.mark.asyncio
async def test_leisure_art_does_not_get_research_progress_or_force_post(env):
    seed_reading(env)
    row = await env.engine.sketchbook.create(env.engine, env.cfg)
    assert row and Path(row["file"]).is_file()
    drawing_prompt = next(p for p in env.prompts if "几何图形自由构图" in p[0])
    assert "不需要知识点" in drawing_prompt[0]
    assert "WHALE_RESEARCH_PROGRESS" not in str(drawing_prompt)
    assert not env.store.get("own_post_requested")
    assert not env.sent


@pytest.mark.asyncio
async def test_completed_art_stays_unpublished_when_sharing_decision_skips(env, monkeypatch):
    row = await env.engine.sketchbook.create(env.engine, env.cfg)
    env.cfg.update(autonomous_posts=True, post_image_probability=0)
    env.response.clear()
    env.response.update(action="skip", messages=[])
    monkeypatch.setattr(autopost, "read_settings", lambda: dict(env.cfg))
    monkeypatch.setattr(autopost, "quiet", lambda now: False)
    poster = autopost.AutoPosts(SimpleNamespace(store=env.store, engine=env.engine))
    assert await poster.run() == "no_content"
    assert not env.engine.sketchbook.records(env.cfg)[0].get("publication")
    assert not env.store.db.execute("SELECT 1 FROM actions WHERE kind='qzone_post'").fetchone()
    assert row["id"] == env.engine.sketchbook.pending_public(env.cfg)["id"]
    decisions = len(env.prompts)
    assert await poster.run() == "waiting"
    assert len(env.prompts) == decisions
