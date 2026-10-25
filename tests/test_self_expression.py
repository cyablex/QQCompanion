import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from engine import Engine
from self_profile import SelfProfile
from sketchbook import Sketchbook, parse_scene, render_scene
from store import Store, TZ


def scene():
    return dict(action="draw", title="米饭小行星", caption="给白米饭装上了星环，饿的时候就抬头看看。", mood="好奇", background="#f8f0e3", elements=[
        dict(type="ellipse", box=[250, 180, 650, 720], fill="#fffdf5", stroke="#324858", width=3),
        dict(type="line", points=[[140, 680], [750, 270]], stroke="#ddb071", width=5),
        dict(type="text", at=[80, 60], text="米饭小行星", size=34, fill="#324858"),
    ])


@pytest.fixture
def env(tmp_path):
    db = Store(tmp_path / "test.sqlite3")
    cfg = dict(owner_qq="12345", bot_qq="67890", name="大肥鱼", persona="喜欢白米饭和有趣的小东西", owner_title="主人", live=True, paused=False, guests=True, revision=1, daily_calls=400, daily_cny=3, monthly_cny=60, autonomous_drawing=True, autonomous_profile=True)
    prompts, sends, writes = [], [], []

    async def generate(system, contexts, **kw):
        prompts.append((system, contexts))
        return json.dumps(scene(), ensure_ascii=False), dict(input_other=10, input_cached=0, output=20)

    async def send(contact, text, **kw):
        sends.append((contact, text, kw))
        return {"message_id": len(sends)}

    engine = Engine(db, generate, send, lambda: dict(cfg))
    engine.sketchbook = Sketchbook(db, root=tmp_path)
    current = {}

    async def check():
        pass

    async def action(name, **kw):
        writes.append((name, kw))
        if name == "set_self_longnick":
            current["signature"] = kw["longNick"]
        if name == "set_online_status":
            current["status"] = (kw["status"], kw["ext_status"])
        if name == "nc_get_user_status":
            return dict(zip(("status", "ext_status"), current["status"]))
        if name == "get_stranger_info":
            return {"long_nick": current["signature"]}

    plugin = SimpleNamespace(store=db, engine=engine, check_account=check, bot=SimpleNamespace(call_action=action))
    yield db, cfg, engine, plugin, prompts, sends, writes
    db.db.close()


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(elements=s["elements"] * 40),
    lambda s: s["elements"][0].update(type="python", code="exec('x')"),
    lambda s: s["elements"][0].update(box=[0, 0, float("nan"), 10]),
    lambda s: s["elements"][0].update(fill="file:///secret"),
    lambda s: s["elements"][0].update(box=[400, 200, 100, 300]),
])
def test_renderer_rejects_invalid_or_executable_input(mutation):
    value = scene()
    mutation(value)
    with pytest.raises(ValueError):
        parse_scene(json.dumps(value))


def test_local_drawing_is_a_nonempty_png(tmp_path):
    destination = tmp_path / "work.png"
    render_scene(parse_scene(json.dumps(scene())), destination)
    with Image.open(destination) as image:
        assert image.size == (960, 640) and image.format == "PNG"
        assert len(image.getcolors(maxcolors=1000000)) > 50


@pytest.mark.asyncio
async def test_private_drawings_never_enter_public_or_other_friend_context(env):
    db, cfg, engine, _, prompts, *_ = env
    db.incoming("secret", "99999", "OTHER_CONTACT_SECRET")
    private = await engine.sketchbook.create(engine, cfg, "PRIVATE_DRAWING_SECRET", "12345")
    assert not db.get("own_post_requested")
    assert private and engine.sketchbook.allowed_file(cfg, private["file"], "12345")
    assert not engine.sketchbook.allowed_file(cfg, private["file"], "99999")
    assert engine.sketchbook.pending_public(cfg) is None
    assert engine.sketchbook.context("99999", cfg) == ""
    assert engine.sketchbook.context("12345", cfg, public=True) == ""
    prompts.clear()
    public = await engine.sketchbook.create(engine, cfg)
    assert not db.get("own_post_requested")
    assert "PRIVATE_DRAWING_SECRET" not in str(prompts) and "OTHER_CONTACT_SECRET" not in str(prompts)
    assert engine.sketchbook.pending_public(cfg)["id"] == public["id"]
    engine.sketchbook.publication(cfg, public["id"], "uncertain-post")
    assert engine.sketchbook.pending_public(cfg) is None
    assert Sketchbook(db, engine.sketchbook.root).pending_public(cfg) is None
    db.forget("12345")
    assert engine.sketchbook.records(cfg, "12345") == []


@pytest.mark.asyncio
async def test_forgetting_while_drawing_cannot_restore_private_art(env):
    db, cfg, engine, *_ = env
    db.incoming("before", "12345", "Forget this")

    async def generate(*args, **kwargs):
        db.forget("12345")
        return json.dumps(scene()), None

    engine.generate = generate
    assert await engine.sketchbook.create(engine, cfg, "private idea", "12345") is None
    assert engine.sketchbook.records(cfg, "12345") == []


@pytest.mark.asyncio
async def test_autonomous_drawing_has_one_daily_attempt_and_quiet_hours(env):
    _, cfg, engine, _, prompts, *_ = env
    now = datetime.now(TZ).replace(hour=12)
    assert await engine.sketchbook.run(engine, now.replace(hour=2)) is None
    assert await engine.sketchbook.run(engine, now)
    assert await engine.sketchbook.run(engine, now) is None
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_chat_draws_attaches_one_image_and_keeps_mood_only(env):
    db, cfg, engine, _, prompts, sends, _ = env
    db.incoming("draw", "12345", "画个白米饭小行星")

    async def generate(system, contexts, **kwargs):
        if "几何图形自由构图" in system:
            return json.dumps(scene()), None
        return "我试试给米饭装上星环。\n[画画:一颗白米饭小行星]\n[心情:好奇]", None

    engine.generate = generate
    assert await engine.reply("12345")
    assert len(sends) == 1
    assert Path(sends[0][2]["image_file"]).is_file()
    assert "[画画" not in sends[0][1] and "[心情" not in sends[0][1]
    mood = db.get("self_mood:67890")
    assert set(mood) == {"mood", "at"} and mood["mood"] == "好奇"


@pytest.mark.asyncio
async def test_drawing_overrides_sticker_and_history_records_actual_image(env):
    db, cfg, engine, _, prompts, sends, _ = env
    from stickers import StickerLibrary

    engine.stickers = StickerLibrary()
    cfg["send_memes"] = True
    db.incoming("draw-with-sticker", "12345", "画一颗小星星给我看看")

    async def generate(system, contexts, **kwargs):
        if "几何图形自由构图" in system:
            return json.dumps(scene()), None
        return "[表情:dsh_deyi]\n[画画:一颗小星星]", None

    engine.generate = generate
    assert await engine.reply("12345")
    assert set(sends[0][2]) == {"image_file"}
    assert db.history("12345")[-1]["content"] == "[发送自己画的简笔画]"
    assert db.get("last_sticker:12345") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope", [False, True])
async def test_repeated_drawing_markers_never_leak_and_only_one_image_is_sent(env, envelope):
    db, cfg, engine, _, prompts, sends, _ = env
    from stickers import StickerLibrary

    cfg["send_memes"] = True
    engine.stickers = StickerLibrary()
    db.incoming("lazycat-drawing", "12345", "画一下你对我的印象图")
    answer = "这是我想象里的猫猫。\n\n[画画:一只猫]\n[心情:好奇]\n[画画:耳朵上有小花的猫]\n[表情:dsh_deyi]"

    async def generate(system, contexts, **kwargs):
        if "几何图形自由构图" in system:
            return json.dumps(scene()), None
        if envelope:
            return json.dumps(dict(action="reply", messages=[answer], memory=[])), None
        return answer, None

    engine.generate = generate
    assert await engine.reply("12345")
    assert len(sends) == 1 and set(sends[0][2]) == {"image_file"}
    assert Path(sends[0][2]["image_file"]).is_file()
    assert "[画画" not in sends[0][1] and "[心情" not in sends[0][1]
    assert "[发送自己画的简笔画]" in db.history("12345")[-1]["content"]
    assert len(engine.sketchbook.records(cfg, "12345")) == 1


@pytest.mark.asyncio
async def test_drawing_marker_only_sends_picture_without_filler(env):
    db, cfg, engine, _, _, sends, _ = env
    db.incoming("picture-only", "12345", "只发画就好")

    async def generate(system, contexts, **kwargs):
        return (json.dumps(scene()) if "几何图形自由构图" in system else "[画画:米饭小行星]"), None

    engine.generate = generate
    assert await engine.reply("12345")
    assert sends[0][1] == "" and "image_file" in sends[0][2]


@pytest.mark.asyncio
async def test_proactive_can_share_completed_public_art_once_per_contact(env, monkeypatch):
    monkeypatch.setattr("engine.quiet", lambda now: False)
    db, cfg, engine, _, _, sends, _ = env
    cfg["proactive"] = True
    drawing = await engine.sketchbook.create(engine, cfg)
    db.incoming("earlier", "12345", "喜欢这种有趣的小东西")

    async def generate(*args, **kwargs):
        return json.dumps(dict(action="reply", messages=["刚画好的米饭小行星，给你看看。"], share_drawing=True)), None

    engine.generate = generate
    result = await engine.decision("12345", "proactive", "有合适话题才分享")
    assert result["drawing_file"] == drawing["file"]
    assert await engine.deliver("12345", "proactive", result)
    assert sends[0][2]["image_file"] == drawing["file"]
    again = await engine.decision("12345", "proactive", "有合适话题才分享")
    assert not again.get("drawing_file")


@pytest.mark.asyncio
async def test_profile_uses_public_context_and_persists_cooldowns(env):
    db, cfg, engine, plugin, prompts, _, writes = env
    db.incoming("private", "12345", "PRIVATE_PROFILE_SECRET")
    db.remember("99999", "OTHER_PERSON_SECRET")

    async def generate(system, contexts, **kw):
        prompts.append((system, contexts))
        return json.dumps(dict(action="update", status="悠哉哉", signature="让白米饭也拥有一片星空。")), None

    engine.generate = generate
    now = datetime.now(TZ).replace(hour=12)
    await SelfProfile(plugin).run(now)
    assert "SECRET" not in str(prompts)
    assert [name for name, _ in writes if name.startswith("set_")] == ["set_online_status", "set_self_longnick"]
    assert writes[0][1]["ext_status"] == 1059 and "battery_status" in writes[0][1]
    assert any(name == "set_self_longnick" and "longNick" in args for name, args in writes)
    assert db.get("self_profile:67890")["signature_result"] == "confirmed"
    writes.clear()
    await SelfProfile(plugin).run(now)
    assert writes == []


@pytest.mark.asyncio
async def test_sleep_status_is_custom_online_and_needs_no_model(env):
    db, cfg, engine, plugin, prompts, _, writes = env
    await SelfProfile(plugin).run(datetime.now(TZ).replace(hour=1))
    assert writes[0] == ("set_online_status", dict(status=10, ext_status=1016, battery_status=100))
    assert prompts == []
    cfg["paused"] = True
    writes.clear()
    await SelfProfile(plugin).run(datetime.now(TZ).replace(hour=9))
    assert writes == []


@pytest.mark.asyncio
async def test_unknown_profile_write_is_not_repeated_after_restart(env):
    db, cfg, engine, plugin, _, _, writes = env

    async def action(name, **kwargs):
        writes.append(name)
        raise TimeoutError()

    plugin.bot.call_action = action
    now = datetime.now(TZ).replace(hour=1)
    await SelfProfile(plugin).run(now)
    await SelfProfile(plugin).run(now)
    assert writes == ["set_online_status"]
    assert db.get("self_profile:67890")["status_result"] == "TimeoutError"
