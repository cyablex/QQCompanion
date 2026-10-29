# ruff: noqa: E402
import asyncio
import hashlib
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "astrbot"))
import autopost
import media
import stickers as stickers_module
from engine import Engine
from media import MediaPipeline, image_frames
from stickers import StickerLibrary
from store import Store


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Synthetic images keep this suite independent of personal/licensed assets.
    sticker_root = tmp_path / "sticker-fixture"
    folder = sticker_root / "assets/memes"
    folder.mkdir(parents=True)
    catalog = []
    for ident, tag, color in [
        ("dsh_deyi", "得意", "red"),
        ("maid_sleep", "趴着睡觉", "blue"),
        ("maid_happy", "开心蹦跳", "green"),
    ]:
        file = folder / (ident + ".png")
        Image.new("RGB", (2, 2), color).save(file)
        catalog.append(
            dict(
                id=ident,
                tag=tag,
                meaning="测试表情",
                file=str(file),
                sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
            )
        )
    (folder / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(stickers_module, "ROOT", sticker_root)
    cfg = dict(
        owner_qq="12345",
        bot_qq="67890",
        name="fish",
        persona="kind",
        owner_title="主人",
        revision=1,
        live=True,
        paused=False,
        guests=True,
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
        send_memes=True,
        image_understanding=True,
        voice_understanding=True,
        autonomous_posts=True,
        post_requires_approval=False,
        post_image_probability=0,
    )
    store = Store(tmp_path / "test.sqlite3")
    calls, sent = [], []

    async def generate(system, contexts, **options):
        calls.append((system, contexts, options))
        return "收到啦\n[表情:dsh_deyi]", None

    async def send(contact, text, **options):
        sent.append((contact, text, options))
        return dict(message_id=len(sent))

    engine = Engine(store, generate, send, lambda: cfg)
    engine.stickers = StickerLibrary()
    plugin = SimpleNamespace(store=store, engine=engine, stickers=engine.stickers)
    monkeypatch.setattr(media, "read_settings", lambda: cfg)
    monkeypatch.setattr(autopost, "read_settings", lambda: cfg)
    monkeypatch.setattr(autopost, "quiet", lambda now: False)
    async def worthy(*args):
        return dict(worthy=True, reason="这组测试单独验证发送协议")
    monkeypatch.setattr(autopost.AutoPosts, "sharing_review", worthy)
    yield cfg, store, engine, plugin, calls, sent
    store.db.close()


def test_image_voice_detection_and_gif_sampling():
    raw = dict(
        message=[dict(type="image", data={"file": "a"})] * 4
        + [dict(type="record", data={"file": "voice"})] * 2
        + [dict(type="face", data={"id": 14})]
    )
    text, attachments = MediaPipeline.describe_input(raw, "")
    assert len(attachments) == 3 and "微笑" in text
    frames = [Image.new("RGB", (20, 20), c) for c in ["red", "green", "blue", "white"]]
    out = io.BytesIO()
    frames[0].save(
        out, format="GIF", save_all=True, append_images=frames[1:], duration=100
    )
    assert len(image_frames(out.getvalue())) == 3


@pytest.mark.asyncio
async def test_caption_cache_is_neutral_and_voice_updates_correct_contact(env):
    cfg, store, engine, plugin, calls, sent = env
    pipe = MediaPipeline(plugin)
    out = io.BytesIO()
    Image.new("RGB", (20, 20), "blue").save(out, format="PNG")

    async def resolve(payload, kind):
        return out.getvalue()

    async def transcribe(data):
        return "今天下午好"

    pipe.resolve = resolve
    pipe.speech.transcribe = transcribe
    for event, contact, kind in [
        ("i", "12345", "image"),
        ("j", "99999", "image"),
        ("v", "12345", "voice"),
    ]:
        store.incoming(event, contact, "附件")
        pipe.capture(event, contact, "", [(kind, {"file": "opaque-id"})])
        await pipe.prepare(contact)
    assert len(calls) == 1
    assert isinstance(calls[0][1][0]["content"], list)
    assert "主人" not in calls[0][0] and "12345" not in str(calls)
    assert "今天下午好" in str(store.history("12345"))
    assert "今天下午好" not in str(store.history("99999"))
    store.forget("12345")
    assert not store.db.execute(
        "SELECT 1 FROM media_inputs WHERE contact='12345'"
    ).fetchone()


@pytest.mark.asyncio
async def test_quota_precedes_media_and_sticker_combines_one_bubble(env):
    cfg, store, engine, plugin, calls, sent = env
    prepared = []

    async def prepare(contact):
        prepared.append(contact)

    engine.prepare = prepare
    store.incoming("i", "12345", "hello")
    assert await engine.reply("12345")
    assert sent[0][2]["sticker_id"] == "dsh_deyi"
    assert len(sent) == 1 and store.sent_count(0, "12345") == 1
    store.incoming("j", "12345", "hello again")
    assert await engine.reply("12345")
    assert sent[1][2] == {}  # omit the same sticker decorating consecutive text
    for n in range(50):
        store.action(f"old{n}", "99999", "reply", ["old"])
        store.outcome(f"old{n}", "succeeded")
    store.incoming("k", "99999", "photo")
    await engine.reply("99999")
    assert prepared == ["12345", "12345"] and len(calls) == 2


@pytest.mark.asyncio
async def test_new_message_during_media_preparation_defers_reply(env):
    cfg, store, engine, plugin, calls, sent = env
    store.incoming("i", "12345", "old")

    async def prepare(contact):
        store.incoming("j", contact, "new image")

    engine.prepare = prepare
    assert not await engine.reply("12345")
    assert not calls and not sent


@pytest.mark.asyncio
async def test_favorites_verified_once_and_untrusted_id_removed(env, monkeypatch):
    cfg, store, engine, plugin, calls, sent = env
    library = engine.stickers
    library.items = dict(list(library.items.items())[:2])
    known, adds = [], []

    async def check():
        pass

    async def action(name, **args):
        if name == "fetch_custom_face_detail":
            return dict(emojiInfoList=[dict(md5=x) for x in known])
        adds.append(args["file"])
        known.append(hashlib.md5(Path(args["file"]).read_bytes()).hexdigest())
        return {}

    async def no_sleep(delay):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    plugin.bot, plugin.check_account = SimpleNamespace(call_action=action), check
    await library.collect(plugin)
    await library.collect(plugin)
    assert len(adds) == 2 and store.get("emote_collection")["confirmed_favorites"] == 2
    assert library.extract("hello\n[表情:../../private]") == (
        "hello\n[表情:../../private]",
        None,
    )
    assert library.extract("hello\n[表情:not_existing]") == ("hello", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain", [False, True])
async def test_own_post_one_per_day_image_payload_no_private_context(
    env, tmp_path, uncertain
):
    cfg, store, engine, plugin, calls, sent = env
    cfg["post_image_probability"] = 1
    store.incoming("private", "12345", "SECRET PRIVATE")
    store.remember("12345", "PRIVATE MEMORY")

    async def generate(system, contexts, **options):
        if "判断自主公开表达" in system:
            return '{"novel":true}', None
        calls.append((system, contexts))
        return json.dumps(
            dict(
                action="reply", messages=[f"小鱼的新想法第{len(calls)}篇。"], memory=[]
            )
        ), None

    engine.generate = generate
    post_calls = []

    async def check():
        pass

    async def action(name, **args):
        post_calls.append((name, args))
        if uncertain:
            raise TimeoutError()
        return dict(tid="posted1")

    async def feeds(*args, **kwargs):
        return SimpleNamespace(ok=True, data=dict(msglist=[]))

    plugin.bot = SimpleNamespace(call_action=action)
    plugin.check_account = check
    plugin.social = SimpleNamespace(
        ensure_qzone=check, qzone_api=SimpleNamespace(get_feeds=feeds)
    )
    poster = autopost.AutoPosts(plugin)
    image = tmp_path / "public.png"
    Image.new("RGB", (10, 10)).save(image)
    poster.images.search = lambda content: dict(
        file=str(image), source="https://commons.wikimedia.org/wiki/File:Public.png"
    )
    if uncertain:
        with pytest.raises(TimeoutError):
            await poster.run()
    else:
        assert await poster.run() == "unknown"  # receipt alone is not readback
    assert await poster.run() == "already_processed_today"
    assert await poster.run(force=True) == "already_processed_today"
    assert len(post_calls) == 1 and len(calls) == 1
    assert "PRIVATE" not in str(calls)
    assert post_calls[0][1]["ugc_right"] == 4
    assert post_calls[0][1]["images"] == [image.as_uri()]


@pytest.mark.asyncio
async def test_own_post_skip_does_not_publish(env, monkeypatch):
    cfg, store, engine, plugin, calls, sent = env
    decisions = []

    async def decision(*args, **kwargs):
        decisions.append(True)
        return dict(action="skip", messages=[])

    engine.decision = decision
    poster = autopost.AutoPosts(plugin)
    assert await poster.run() == "no_content"
    later = autopost.time.time() + 4 * 3600
    monkeypatch.setattr(autopost.time, "time", lambda: later)
    assert await poster.run() == "waiting"
    assert len(decisions) == 1
    assert not store.db.execute(
        "SELECT 1 FROM actions WHERE kind='qzone_post'"
    ).fetchone()


@pytest.mark.asyncio
async def test_own_drawing_publishes_its_image_without_private_context_or_stock_search(
    env, tmp_path
):
    cfg, store, engine, plugin, calls, sent = env
    cfg["autonomous_drawing"] = True
    from sketchbook import Sketchbook

    engine.sketchbook = Sketchbook(store, tmp_path)
    image = engine.sketchbook.folder(cfg) / "public-art.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (20, 20), "blue").save(image)
    row = dict(
        id="public-art",
        file=str(image),
        title="饭粒宇宙",
        caption="白米饭也想看看月亮。",
        created=0,
        scope="public",
    )
    store.set(engine.sketchbook.key(cfg), [row])

    async def choose_art(system, contexts, **kwargs):
        calls.append((system, contexts))
        return json.dumps(
            dict(action="reply", messages=[row["caption"]], share_drawing=True)
        ), None

    engine.generate = choose_art
    # New completed work can be shared even if an earlier assessment had no idea.
    day = autopost.datetime.now(autopost.TZ).strftime("%Y-%m-%d")
    store.set(
        f"qzone_post_attempt:{cfg['bot_qq']}:{day}",
        dict(count=1, last=autopost.time.time()),
    )
    store.incoming("private-illustration", "12345", "PRIVATE_ART_SECRET")
    post_calls = []

    async def check():
        pass

    async def action(name, **args):
        post_calls.append(args)
        return dict(tid="drawing-post")

    async def feeds(*args, **kwargs):
        return SimpleNamespace(ok=True, data=dict(msglist=[]))

    plugin.bot = SimpleNamespace(call_action=action)
    plugin.check_account = check
    plugin.social = SimpleNamespace(
        ensure_qzone=check, qzone_api=SimpleNamespace(get_feeds=feeds)
    )
    poster = autopost.AutoPosts(plugin)

    def no_stock_search(content):
        raise AssertionError("An original drawing must use its own PNG")

    poster.images.search = no_stock_search
    assert await poster.run() == "unknown"
    assert post_calls[0]["images"] == [image.as_uri()]
    assert post_calls[0]["content"] == row["caption"]
    assert engine.sketchbook.pending_public(cfg) is None
    assert len(calls) == 1 and "PRIVATE_ART_SECRET" not in str(calls)


@pytest.mark.asyncio
async def test_sticker_only_is_one_bubble_without_fallback_and_remains_in_history(env):
    cfg, store, engine, plugin, calls, sent = env
    store.message("12345", "assistant", "好呀～")
    store.incoming("bye", "12345", "我先走了，拜拜")

    async def generate(system, contexts, **options):
        assert cfg["persona"] in system
        assert "只输出这个标记" in system
        return "[表情:maid_sleep]", None

    engine.generate = generate
    assert await engine.reply("12345")
    assert sent == [("12345", "", {"sticker_id": "maid_sleep"})]
    assert store.sent_count(0, "12345") == 1
    assert store.history("12345")[-1]["content"] == "[发送鲸鱼娘表情:趴着睡觉]"
    assert not await engine.reply("12345")


@pytest.mark.asyncio
async def test_different_stickers_can_follow_without_two_minute_cooldown(env):
    cfg, store, engine, plugin, calls, sent = env
    outputs = iter(["好呀\n[表情:dsh_deyi]", "拜拜\n[表情:maid_sleep]"])

    async def generate(system, contexts, **options):
        return next(outputs), None

    engine.generate = generate
    for n in range(2):
        store.incoming(str(n), "12345", "test")
        assert await engine.reply("12345")
    assert [row[2]["sticker_id"] for row in sent] == ["dsh_deyi", "maid_sleep"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contact,enabled", [("12345", True), ("99999", True), ("12345", False)]
)
async def test_new_animation_delivers_with_existing_switch_and_contact_scope(
    env, contact, enabled
):
    cfg, store, engine, plugin, calls, sent = env
    cfg["send_memes"] = enabled
    store.incoming("new-emote", contact, "哈哈哈这也太好玩了")

    async def generate(system, contexts, **options):
        assert "女性AI虚拟角色" not in system
        assert "如实说明是 AI" not in system
        if enabled:
            assert "maid_happy=开心蹦跳" in system
            assert "085_master=主人" not in system
        else:
            assert "可选鲸鱼娘表情" not in system
        return "笑死我啦\n[表情:maid_happy]", None

    engine.generate = generate
    assert await engine.reply(contact)
    assert sent == [
        (contact, "笑死我啦", {"sticker_id": "maid_happy"} if enabled else {})
    ]
    assert store.sent_count(0, contact) == 1
    if enabled:
        assert store.get("last_sticker:" + contact)["id"] == "maid_happy"


@pytest.mark.asyncio
async def test_standalone_sticker_is_not_swallowed_when_same_sticker_was_recent(env):
    cfg, store, engine, plugin, calls, sent = env
    store.set("last_sticker:12345", {"id": "maid_sleep", "at": media.time.time()})
    store.incoming("bye-again", "12345", "拜拜")

    async def generate(*args, **options):
        return "[QQ表情:比心]\n[表情:maid_sleep]", None

    engine.generate = generate
    assert await engine.reply("12345")
    assert sent == [("12345", "", {"sticker_id": "maid_sleep"})]


@pytest.mark.asyncio
async def test_legacy_first_post_prevents_another_post_today(env, monkeypatch):
    cfg, store, engine, plugin, calls, sent = env
    poster = autopost.AutoPosts(plugin)
    day = autopost.datetime.now(autopost.TZ).strftime("%Y-%m-%d")
    first = f"qzone_post:{cfg['bot_qq']}:{day}"
    store.action(first, f"qzone:{cfg['bot_qq']}", "qzone_post", ["old"])
    store.outcome(first, "succeeded")
    assert poster.next_slot(cfg, day)[0] is None
    assert await poster.run(force=True) == "already_processed_today"


@pytest.mark.asyncio
async def test_duplicate_post_content_is_not_sent_again(env):
    cfg, store, engine, plugin, calls, sent = env
    poster = autopost.AutoPosts(plugin)
    store.action(
        f"qzone_post:{cfg['bot_qq']}:previous-day",
        f"qzone:{cfg['bot_qq']}",
        "qzone_post",
        ["白米饭像云朵。\n\n配图来源：https://example.org"],
    )

    async def same(*args, **kwargs):
        return dict(action="reply", messages=["白米饭像云朵。"], revision=1)

    engine.decision = same
    assert await poster.run(force=True) == "duplicate_content"


@pytest.mark.asyncio
async def test_concurrent_posters_cannot_publish_same_content_twice(env, monkeypatch):
    cfg, store, engine, plugin, calls, sent = env
    from qzone_vendor.core.qzone import QzoneParser

    arrived = 0
    gate = asyncio.Event()
    sends = []

    async def decision(*args, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            gate.set()
        await gate.wait()
        return dict(action="reply", messages=["同一份新作品。"], revision=1)

    async def check():
        pass

    async def action(name, **kwargs):
        sends.append(kwargs)
        return dict(tid="confirmed")

    async def feeds(*args, **kwargs):
        return SimpleNamespace(ok=True, data={"msglist": []})

    monkeypatch.setattr(
        QzoneParser,
        "parse_feeds",
        lambda _: [SimpleNamespace(tid="confirmed", text="同一份新作品。")],
    )
    engine.decision = decision
    plugin.check_account = check
    plugin.bot = SimpleNamespace(call_action=action)
    plugin.social = SimpleNamespace(
        ensure_qzone=check, qzone_api=SimpleNamespace(get_feeds=feeds)
    )
    poster = autopost.AutoPosts(plugin)
    result = await asyncio.gather(poster.run(force=True), poster.run(force=True))
    assert sorted(result) == ["already_processed_today", "succeeded"]
    assert len(sends) == 1
