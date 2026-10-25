"""Voice changes must preserve delivery cancellation and receipt semantics."""

import asyncio
import importlib.util
import os
import threading
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from engine import Engine, DeliveryUnavailable  # noqa: E402
from local_voice import LocalVoice, wants_voice  # noqa: E402
import local_voice  # noqa: E402
from store import Store  # noqa: E402


def test_spoken_sentences_preserve_boundaries_and_vocative():
    from local_voice import synthesis_text

    assert synthesis_text("主人，你回来啦。\n别着急，我们慢慢聊。") == "主人你回来啦。 别着急，我们慢慢聊。"
    assert synthesis_text("你回来啦！今天好吗？好，那就慢慢说。") == "你回来啦！今天好吗？好，那就慢慢说。"
    assert synthesis_text("不，别去。") == "不，别去。"
    assert synthesis_text("亲爱的，先歇会儿。", "亲爱的") == "亲爱的先歇会儿。"


def test_voice_stays_loaded_past_half_hour_until_explicit_close(monkeypatch):
    clock, timers, closed = [0], [], []

    class Timer:
        def __init__(self, delay, callback):
            self.due, self.callback, self.cancelled = clock[0] + delay, callback, False
        def start(self):
            timers.append(self)
        def cancel(self):
            self.cancelled = True

    def advance(seconds):
        clock[0] += seconds
        for timer in timers:
            if not timer.cancelled and timer.due <= clock[0]:
                timer.cancelled = True
                timer.callback()

    voice = LocalVoice(None)
    monkeypatch.setattr(local_voice.threading, "Timer", Timer)
    monkeypatch.setattr(voice, "_run", lambda *args: "voice.wav")
    monkeypatch.setattr(voice, "close", lambda: closed.append(True))
    voice._synthesize("你好呀", "everyday")
    advance(181)
    assert not closed
    voice._synthesize("继续聊", "everyday")
    advance(1800 - 181)
    assert not closed
    advance(181)
    advance(86400)
    assert not closed and not timers
    voice.close()
    assert closed == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    "那先歇一会儿，我陪你慢慢缓过来。\n[语音]\n[心情:平静]",
    "[语音] 那先歇一会儿，我陪你慢慢缓过来。",
    "[语音]那先歇一会儿，我陪你慢慢缓过来。",
    "那先歇一会儿，我陪你慢慢缓过来。[语音]",
])
async def test_adaptive_voice_follows_reply_choice_and_never_leaks_marker(voice_engine, monkeypatch, reply):
    engine, store, cfg, sends, file = voice_engine
    cfg["voice_mode"] = "adaptive"
    cfg.update(daily_calls=1000, daily_cny=3, monthly_cny=60)
    store.incoming("natural", "12345", "今天有点累，想安静待会儿")
    # Exercise generation/parsing as well as synthesis selection.
    engine.decision = Engine.decision.__get__(engine)
    async def generate(*args, **kwargs):
        return reply, None
    engine.generate = generate
    monkeypatch.setattr(engine.voice, "_synthesize", lambda *args: str(file))
    assert await engine.reply("12345")
    assert len(sends) == 1 and sends[0][2]["voice_file"] == str(file)
    assert "[语音]" not in sends[0][1] and "[心情" not in sends[0][1]
    assert store.get("last_delivery:12345")["mode"] == "voice"


def test_voice_marker_inside_quoted_explanation_is_not_a_send_directive():
    text = '你说的“[语音]”只是标签，不能直接听。'
    assert not local_voice.VOICE_MARK.search(text)
    assert local_voice.VOICE_MARK.sub("", text) == text


@pytest.mark.asyncio
async def test_prewarm_makes_no_send_and_busy_warmup_does_not_queue_chat(voice_engine, monkeypatch):
    engine, store, cfg, sends, file = voice_engine
    started, release = threading.Event(), threading.Event()
    def slow(*args):
        started.set()
        assert release.wait(3)
        return str(file)
    monkeypatch.setattr(engine.voice, "_ready", lambda: False)
    monkeypatch.setattr(engine.voice, "_synthesize", slow)
    task = asyncio.create_task(engine.voice.prewarm())
    try:
        assert await asyncio.to_thread(started.wait, 1)
        result = await engine.decision("12345", "reply")
        assert not (await engine.voice.prepare("12345", result)).get("voice_file")
        assert not sends
        release.set()
        await task
        assert store.get("local_voice_status")["state"] == "warmed"
        assert not sends
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_existing_http_endpoint_still_requires_real_first_inference(voice_engine, monkeypatch):
    engine, store, cfg, sends, file = voice_engine
    calls = []
    monkeypatch.setattr(engine.voice, "_ready", lambda: True)
    monkeypatch.setattr(engine.voice, "_synthesize", lambda *args: calls.append(args) or str(file))
    assert await engine.voice.prewarm()
    assert len(calls) == 1 and engine.voice.warmed and not sends
    assert await engine.voice.prewarm()
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", ["voice_replies", "game_mode", "paused", "live"])
async def test_prewarm_honors_voice_game_and_pause_settings(voice_engine, monkeypatch, disabled):
    engine, store, cfg, sends, file = voice_engine
    cfg[disabled] = disabled in {"game_mode", "paused"}
    monkeypatch.setattr(engine.voice, "_synthesize", lambda *args: pytest.fail("must not warm"))
    await engine.voice.prewarm()
    assert not sends and engine.voice.pending is None


@pytest.mark.asyncio
async def test_adaptive_voice_respects_text_request(voice_engine, monkeypatch):
    engine, store, cfg, sends, file = voice_engine
    cfg["voice_mode"] = "adaptive"
    store.incoming("text-only", "12345", "不用语音，文字回复就好")
    def forbidden(*args):
        raise AssertionError("must not synthesize")
    monkeypatch.setattr(engine.voice, "_synthesize", forbidden)
    result = dict(action="reply", messages=["好，慢慢说。"], voice_selected=True)
    assert not (await engine.voice.prepare("12345", result)).get("voice_file")


@pytest.mark.parametrize("prompt_lang", ["ja", "all_zh"])
def test_local_api_uses_reference_language_and_preserves_sentence_boundaries(tmp_path, monkeypatch, prompt_lang):
    import io
    import json
    import wave

    monkeypatch.setattr(local_voice, "ROOT", tmp_path)
    monkeypatch.setattr(local_voice, "TTS_ROOT", tmp_path / "tts")
    manifest = tmp_path / "tts/reference/yui/presets.json"
    manifest.parent.mkdir(parents=True)
    prompt = "你回来啦，今天过得怎么样？别着急，我们慢慢聊。" if prompt_lang == "all_zh" else "reference"
    manifest.write_text(json.dumps({"profiles": [dict(id="everyday", ref_audio_path="reference.wav", prompt_text=prompt, prompt_lang=prompt_lang)]}), encoding="utf-8")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 16000)
    payloads = []

    def response(request, **kwargs):
        payloads.append(json.loads(request.data))
        return io.BytesIO(audio.getvalue())

    voice = LocalVoice(None, lambda: {"owner_title": "主人"})
    voice.http = SimpleNamespace(open=response)
    monkeypatch.setattr(voice, "_ready", lambda: True)
    file = voice._run("主人，你回来啦。今天开心吗？", "everyday")
    assert Path(file).is_file()
    assert payloads[0]["text"] == "主人你回来啦。今天开心吗？"
    assert payloads[0]["ref_audio_path"] == "reference.wav"
    assert payloads[0]["prompt_lang"] == prompt_lang
    assert payloads[0]["prompt_text"] == prompt
    assert payloads[0]["text_split_method"] == "cut0"
    assert payloads[0]["fragment_interval"] == 0.12


@pytest.fixture
def voice_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(local_voice, "ROOT", tmp_path)
    cfg = dict(owner_qq="12345", bot_qq="67890", live=True, paused=False,
               guests=True, guest_ids=["99999"], revision=1, voice_replies=True,
               voice_mode="requested", voice_style="auto")
    store = Store(tmp_path / "test.sqlite3")
    sends = []

    async def send(contact, text, **kwargs):
        sends.append((contact, text, kwargs))
        return {"message_id": 123}

    engine = Engine(store, None, send, lambda: dict(cfg))
    engine.voice = LocalVoice(store, lambda: dict(cfg))
    file = tmp_path / "data/voice/test.wav"
    file.parent.mkdir(parents=True)
    file.write_bytes(b"stub")
    store.incoming("owner-1", "12345", "用语音说给我听")

    async def decision(*args):
        return dict(action="reply", messages=["你回来啦。", "我们慢慢聊。"],
                    revision=cfg["revision"], read_through=store.last_incoming("12345"))

    engine.decision = decision
    monkeypatch.setattr(engine.voice, "_synthesize", lambda text, style: str(file))
    yield engine, store, cfg, sends, file
    engine.voice.close()
    store.db.close()


def test_owner_request_and_game_boundaries():
    cfg = dict(owner_qq="12345", voice_replies=True)
    assert wants_voice(cfg, "12345", "发段语音吧")
    assert wants_voice(cfg, "12345", "说一句话我听听")
    assert wants_voice(cfg, "12345", "讲两句让我听一下")
    assert not wants_voice(cfg, "99999", "发段语音吧")
    assert not wants_voice(cfg, "12345", "我研究了一下语音模型")
    assert not wants_voice(dict(cfg, game_mode=True), "12345", "发段语音吧")
    assert not wants_voice(dict(cfg, voice_mode="always"), "12345", "这次不要语音，用文字")


def test_retired_lively_voice_cannot_start_inference():
    voice = LocalVoice(None)
    with pytest.raises(ValueError, match="retired"):
        voice._run("好耶！", "lively")


@pytest.mark.asyncio
async def test_legacy_auto_preference_stays_on_everyday_voice(voice_engine, monkeypatch):
    engine, store, cfg, sends, file = voice_engine
    used = []

    def synthesize(text, style):
        used.append(style)
        return str(file)

    monkeypatch.setattr(engine.voice, "_synthesize", synthesize)
    result = dict(action="reply", messages=["好耶！太棒啦！"])
    await engine.voice.prepare("12345", result)
    assert used == ["everyday"]


@pytest.mark.asyncio
async def test_voice_is_one_bubble_and_history_retains_spoken_words(voice_engine):
    engine, store, cfg, sends, file = voice_engine
    assert await engine.reply("12345")
    assert sends == [("12345", "你回来啦。\n我们慢慢聊。", {"voice_file": str(file)})]
    assert store.history("12345")[-1]["content"] == "[语音] 你回来啦。\n我们慢慢聊。"
    assert not await engine.reply("12345")
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_synthesis_failure_falls_back_before_any_send(voice_engine, monkeypatch):
    engine, store, cfg, sends, file = voice_engine

    def fail(*args):
        raise TimeoutError()

    monkeypatch.setattr(engine.voice, "_synthesize", fail)
    assert await engine.reply("12345")
    assert len(sends) == 2 and all(not s[2] for s in sends)
    assert store.get("local_voice_status")["state"] == "text_fallback"
    assert store.get("last_delivery:12345")["mode"] == "text"


@pytest.mark.asyncio
async def test_slow_voice_falls_back_without_queueing_more_or_late_sends(voice_engine, monkeypatch):
    engine, store, cfg, sends, file = voice_engine
    release = threading.Event()
    calls = []

    def slow(*args):
        calls.append(True)
        assert release.wait(3)
        return str(file)

    monkeypatch.setattr(local_voice, "SYNTHESIS_WAIT_SECONDS", 0.03)
    monkeypatch.setattr(engine.voice, "_synthesize", slow)
    pending = None
    try:
        result = await engine.decision("12345", "reply")
        first = await asyncio.wait_for(engine.voice.prepare("12345", result), 0.5)
        assert not first.get("voice_file")
        assert store.get("local_voice_status")["reason"] == "synthesis_slow"
        pending = engine.voice.pending
        assert pending and not pending.done()
        store.incoming("new-voice", "12345", "再用语音说一句吧")
        result = await engine.decision("12345", "reply")
        second = await asyncio.wait_for(engine.voice.prepare("12345", result), 0.3)
        assert not second.get("voice_file") and len(calls) == 1
        assert store.get("local_voice_status")["reason"] == "synthesis_busy"
        release.set()
        await pending
        assert sends == []  # finishing in the background cannot initiate delivery
        monkeypatch.setattr(engine.voice, "_synthesize", lambda *args: str(file))
        ready = await engine.voice.prepare("12345", result)
        assert ready["voice_file"] == str(file)
    finally:
        release.set()
        if pending:
            await pending


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["new_message", "pause", "forget"])
async def test_new_input_or_revocation_interrupts_voice_wait_before_synthesis_finishes(voice_engine, monkeypatch, change):
    engine, store, cfg, sends, file = voice_engine
    started, release = threading.Event(), threading.Event()

    def slow(*args):
        started.set()
        assert release.wait(3)
        return str(file)

    monkeypatch.setattr(engine.voice, "_synthesize", slow)
    result = await engine.decision("12345", "reply")
    result["memory_generation"] = engine.memory_epoch("12345")
    task = asyncio.create_task(engine.voice.prepare("12345", result))
    pending = None
    try:
        assert await asyncio.to_thread(started.wait, 1)
        pending = engine.voice.pending
        if change == "new_message":
            store.incoming("fresh", "12345", "还在吗")
        elif change == "pause":
            cfg.update(paused=True, revision=2)
        else:
            store.forget("12345")
        answer = await asyncio.wait_for(task, 0.8)
        assert not answer.get("voice_file") and not pending.done()
        assert sends == []
    finally:
        release.set()
        if pending:
            await pending


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["new_message", "pause", "game", "forget"])
async def test_slow_synthesis_cannot_send_obsolete_reply(voice_engine, monkeypatch, change):
    engine, store, cfg, sends, file = voice_engine
    started, finish = asyncio.Event(), asyncio.Event()

    async def prepare(contact, result):
        result = dict(result, memory_generation=engine.memory_epoch(contact))
        started.set()
        await finish.wait()
        return dict(result, messages=["我们慢慢聊。"], voice_file=str(file))

    monkeypatch.setattr(engine.voice, "prepare", prepare)
    task = asyncio.create_task(engine.reply("12345"))
    await started.wait()
    if change == "new_message":
        store.incoming("owner-2", "12345", "先等一下")
    elif change == "forget":
        store.forget("12345")
    else:
        cfg.update(paused=change == "pause", game_mode=change == "game", revision=2)
    finish.set()
    assert not await task
    assert sends == []


@pytest.mark.asyncio
async def test_uncertain_voice_receipt_never_falls_back_to_duplicate_text(voice_engine):
    engine, store, cfg, sends, file = voice_engine

    async def uncertain(*args, **kwargs):
        sends.append(kwargs)
        raise TimeoutError()

    engine.send = uncertain
    with pytest.raises(TimeoutError):
        await engine.reply("12345")
    assert store.has_unknown("12345")
    assert store.get("last_delivery:12345")["mode"] == "unknown"
    assert not await engine.reply("12345")
    assert len(sends) == 1


@pytest.mark.asyncio
async def test_adapter_emits_native_record_and_rejects_other_files(voice_engine, monkeypatch):
    engine, store, cfg, sends, file = voice_engine
    sys.path.insert(0, str(ROOT / "astrbot"))
    os.environ["QQCOMPANION_ROOT"] = str(ROOT)
    spec = importlib.util.spec_from_file_location("voice_adapter_test", ROOT / "plugins/astrbot_plugin_qq_companion/main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_settings", lambda: cfg)
    payloads = []

    async def check():
        pass

    async def action(name, **kwargs):
        payloads.append(kwargs)
        return {"message_id": 1}

    plugin = module.QQCompanion.__new__(module.QQCompanion)
    plugin.check_messaging = check
    plugin.bot = SimpleNamespace(call_action=action)
    await plugin.send_private("12345", "我们慢慢聊。", voice_file=str(file))
    assert payloads[0]["message"] == [{"type": "record", "data": {"file": file.as_uri()}}]
    for contact, path in [("99999", file), ("12345", ROOT / "assets/voice/welcome.wav")]:
        with pytest.raises(DeliveryUnavailable):
            await plugin.send_private(contact, "hi", voice_file=str(path))
    assert len(payloads) == 1
