"""Exercise the installed message handler's open-friend boundary."""

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "astrbot"))
os.environ["QQCOMPANION_ROOT"] = str(ROOT)

from engine import Engine  # noqa: E402
from reply_queue import ReplyQueue  # noqa: E402
from store import Store  # noqa: E402


@pytest.mark.asyncio
async def test_qq_and_desktop_enqueue_use_same_durable_worker(tmp_path):
    spec = importlib.util.spec_from_file_location(
        'companion_shared_desktop_queue_test', ROOT / 'plugins/astrbot_plugin_qq_companion/main.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    plugin = module.QQCompanion.__new__(module.QQCompanion)
    store = Store(tmp_path / 'shared-queue.sqlite3')
    plugin.store, plugin.reply_queue = store, ReplyQueue(store)
    plugin.pending, plugin.workers = {}, {}
    started = asyncio.Event()

    async def drain(contact):
        started.set()
        await asyncio.Future()

    plugin.drain = drain
    try:
        worker = plugin.enqueue_reply('12345', 'vpet:' + 'a' * 32)
        await started.wait()
        assert store.get('reply_job:12345')['event'].startswith('vpet:')
        assert plugin.enqueue_reply('12345', 'qq-followup') is worker
        assert store.get('reply_job:12345')['event'] == 'qq-followup'
        assert plugin.pending['12345'] == 'qq-followup'
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    finally:
        store.db.close()


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_friend_space_scan_before_session_close(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "companion_social_shutdown_test", ROOT / "plugins/astrbot_plugin_qq_companion/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    plugin = module.QQCompanion.__new__(module.QQCompanion)
    store = Store(tmp_path / "shutdown.sqlite3")
    started, stopped = asyncio.Event(), asyncio.Event()
    closed = []

    async def scan():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def close_social():
        assert stopped.is_set()
        assert store.db.execute("SELECT 1").fetchone()
        closed.append("social")

    async def close_groups():
        pass

    async def close_singing():
        assert plugin.singing_task.cancelled()
        assert store.db.execute("SELECT 1").fetchone()
        closed.append("singing")

    plugin.store = store
    plugin.social = SimpleNamespace(daily_qzone=scan, close=close_social)
    plugin.groups = SimpleNamespace(close=close_groups)
    plugin.singing = SimpleNamespace(close=close_singing)
    plugin.singing_task = asyncio.create_task(asyncio.Event().wait())
    plugin.workers, plugin.memory_tasks = {}, {}
    for name in ("loop_task", "offline_task", "own_space_task", "activity_task", "followup_task", "expression_task"):
        setattr(plugin, name, None)
    plugin.qzone_task = asyncio.create_task(plugin.check_friend_space())
    await started.wait()
    await plugin.terminate()
    assert plugin.qzone_task.cancelled() and closed == ["singing", "social"]


@pytest.mark.asyncio
async def test_drawing_reaches_onebot_as_image_instead_of_sticker_or_description(tmp_path, monkeypatch):
    import json
    from engine import DeliveryUnavailable
    from sketchbook import Sketchbook
    from stickers import StickerLibrary

    spec = importlib.util.spec_from_file_location(
        "companion_drawing_adapter_test", ROOT / "plugins/astrbot_plugin_qq_companion/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cfg = dict(owner_qq="12345", bot_qq="67890", name="fish", persona="playful",
               owner_title="主人", live=True, paused=False, guests=True, revision=1,
               daily_calls=400, daily_cny=3, monthly_cny=60,
               autonomous_drawing=True, send_memes=True)
    monkeypatch.setattr(module, "read_settings", lambda: cfg)
    sent = []

    async def check():
        pass

    async def action(name, **kwargs):
        sent.append((name, kwargs))
        return {"message_id": len(sent)}

    async def generate(system, contexts, **kwargs):
        if "几何图形自由构图" in system:
            return json.dumps(dict(action="draw", title="小花猫", caption="想象里的猫。",
                elements=[dict(type="ellipse", box=[100, 100, 300, 400], fill="#123456")] * 3)), None
        return "这只猫是我想象的。\n[画画:小花猫]\n[画画:小花猫]\n[表情:maid_sleep]", None

    store = Store(tmp_path / "adapter.sqlite3")
    try:
        plugin = module.QQCompanion.__new__(module.QQCompanion)
        plugin.check_messaging = check
        plugin.stickers = StickerLibrary()
        plugin.bot = SimpleNamespace(call_action=action)
        plugin.engine = Engine(store, generate, plugin.send_private, lambda: cfg)
        plugin.engine.stickers = plugin.stickers
        plugin.engine.sketchbook = Sketchbook(store, tmp_path)
        store.incoming("request-art", "12345", "画一下你对我的印象图")
        assert await plugin.engine.reply("12345")
        assert len(sent) == 1 and sent[0][0] == "send_private_msg"
        parts = sent[0][1]["message"]
        pictures = [p for p in parts if p["type"] == "image"]
        assert len(pictures) == 1 and "sub_type" not in pictures[0]["data"]
        file = plugin.engine.sketchbook.records(cfg, "12345")[0]["file"]
        assert pictures[0]["data"]["file"] == Path(file).as_uri()
        assert "[画画" not in str(parts) and "[表情" not in str(parts)
        with pytest.raises(DeliveryUnavailable, match="Drawing unavailable"):
            await plugin.send_private("12345", "给你画好了", image_file=str(tmp_path / "missing.png"), sticker_id="maid_sleep")
        assert len(sent) == 1
    finally:
        store.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_private_sticker_only_payload_has_no_filler_text(tmp_path, monkeypatch, enabled):
    from engine import DeliveryUnavailable

    spec = importlib.util.spec_from_file_location(
        "companion_sticker_adapter_test", ROOT / "plugins/astrbot_plugin_qq_companion/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_settings", lambda: {"send_memes": enabled})
    sent = []

    async def check():
        pass

    async def action(name, **kwargs):
        sent.append((name, kwargs))
        return {"message_id": 1}

    plugin = module.QQCompanion.__new__(module.QQCompanion)
    plugin.check_messaging = check
    plugin.stickers = SimpleNamespace(items={"maid_sleep": {"file": str(tmp_path / "wave.png")}})
    plugin.bot = SimpleNamespace(call_action=action)
    if enabled:
        await plugin.send_private("12345", "", sticker_id="maid_sleep")
        assert len(sent) == 1
        assert [segment["type"] for segment in sent[0][1]["message"]] == ["image"]
    else:
        with pytest.raises(DeliveryUnavailable, match="Empty message"):
            await plugin.send_private("12345", "", sticker_id="maid_sleep")
        assert sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize("attachment", [None, "image", "record"])
async def test_open_mode_replies_to_real_friends_and_ignores_temporary_strangers(
    tmp_path, monkeypatch, attachment
):
    spec = importlib.util.spec_from_file_location(
        "companion_adapter_test", ROOT / "plugins/astrbot_plugin_qq_companion/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cfg = dict(
        live=True,
        paused=False,
        owner_qq="12345",
        bot_qq="67890",
        guests=True,
        guest_mode="open",
        revision=1,
    )
    monkeypatch.setattr(module, "read_settings", lambda: cfg)
    store = Store(tmp_path / "adapter.sqlite3")
    store.set("known_friend_ids", ["11111"])
    handled, reads = [], []

    async def unused(*args):
        raise AssertionError("No model or QQ send should be used by this handler test")

    async def read_friends():
        reads.append(True)

    async def drain(contact):
        handled.append(contact)

    plugin = module.QQCompanion.__new__(module.QQCompanion)
    plugin.store = store
    plugin.engine = Engine(store, unused, unused, lambda: cfg)
    plugin.reply_queue = ReplyQueue(store)
    plugin.pending, plugin.workers = {}, {}
    plugin.social = SimpleNamespace(reconcile_friends=read_friends)
    plugin.media = module.MediaPipeline(plugin)
    plugin.drain = drain

    def event(contact):
        return SimpleNamespace(
            message_obj=SimpleNamespace(
                raw_message={
                    "self_id": 67890,
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": int(contact),
                    "message_id": int(contact),
                    "message": [{"type": attachment, "data": {"file": "opaque-id"}}]
                    if attachment
                    else [],
                }
            ),
            bot=object(),
            message_str="" if attachment else "hello",
            stop_event=lambda: None,
        )

    await plugin.receive(event("11111"))
    await asyncio.gather(*plugin.workers.values())
    await plugin.receive(event("22222"))
    assert handled == ["11111"]
    assert reads == [True]
    assert store.history("11111")
    assert store.history("22222") == []
    assert store.get("reply_job:11111")["event"]
    if attachment:
        assert store.db.execute("SELECT count(*) FROM media_inputs").fetchone()[0] == 1
    store.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [False, True])
async def test_friend_frequency_command_respects_reply_quota_but_always_applies(
    tmp_path, monkeypatch, full
):
    spec = importlib.util.spec_from_file_location(
        "companion_command_test", ROOT / "plugins/astrbot_plugin_qq_companion/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cfg = dict(
        live=True,
        paused=False,
        owner_qq="12345",
        bot_qq="67890",
        guests=True,
        guest_mode="open",
        revision=1,
        guest_daily_messages=1,
        daily_chat_messages=200,
    )
    monkeypatch.setattr(module, "read_settings", lambda: cfg)
    store = Store(tmp_path / "command.sqlite3")
    store.set("known_friend_ids", ["11111"])
    sent = []

    async def no_model(*args, **kwargs):
        raise AssertionError("Control commands never call the model")

    async def send(contact, text, **kwargs):
        sent.append((contact, text))
        return {"message_id": len(sent)}

    plugin = module.QQCompanion.__new__(module.QQCompanion)
    plugin.store = store
    plugin.engine = Engine(store, no_model, send, lambda: cfg)
    if full:
        store.action("previous", "11111", "reply", ["previous reply"])
        store.outcome("previous", "succeeded")
    event = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message={
                "self_id": 67890,
                "post_type": "message",
                "message_type": "private",
                "user_id": 11111,
                "message_id": 99,
                "message": [],
            }
        ),
        bot=object(),
        message_str="/主动频率 关闭",
        stop_event=lambda: None,
    )
    await plugin.receive(event)
    assert plugin.engine.memory.preference("11111")["daily_cap"] == 0
    assert len(sent) == (0 if full else 1)
    assert store.sent_count(0, "11111") == 1
    store.db.close()
