# ruff: noqa: E402
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import services
import voice_service
import voice_status
from voice_service import VoiceService
from test_local_voice import voice_engine as voice_engine


@pytest.fixture
def shared(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    cfg = dict(voice_replies=True, live=True, paused=False, game_mode=False)
    released = []
    monkeypatch.setattr(voice_service, "ROOT", tmp_path)
    monkeypatch.setattr(voice_status, "ROOT", tmp_path)
    monkeypatch.setattr(voice_service, "close_local_voice", lambda **kwargs: released.append(kwargs))
    monkeypatch.setattr(voice_service, "active_singing_job", lambda: None)

    class Voice:
        warmed = False
        calls = 0
        process = None

        async def prewarm(self):
            self.calls += 1
            self.warmed = True
            return True

        def close(self):
            self.warmed = False

    voice = Voice()
    return VoiceService(voice, lambda: cfg), voice, cfg, tmp_path, released


@pytest.mark.asyncio
async def test_warmup_runs_without_qq_and_only_real_inference_releases_gate(shared):
    service, voice, cfg, root, _ = shared
    release = asyncio.Event()

    async def slow():
        voice.calls += 1
        await release.wait()
        voice.warmed = True
        return True

    voice.prewarm = slow
    await service.tick()
    await asyncio.sleep(0)
    process = SimpleNamespace(pid=json.loads((root / "data/voice-service.json").read_text())["pid"])
    assert not voice_status.read_voice_status(cfg, process)["ready"]
    release.set()
    await service.task
    await service.tick()
    assert voice_status.read_voice_status(cfg, process)["ready"]
    assert voice.calls == 1
    await service.release()


@pytest.mark.asyncio
async def test_ready_voice_remains_loaded_for_day_without_rewarming(shared, monkeypatch):
    service, voice, cfg, root, _ = shared
    await service.tick()
    await service.task
    await service.tick()
    now = time.time()
    monkeypatch.setattr(voice_service, "time", SimpleNamespace(time=lambda: now + 86400))
    await service.tick()
    assert service.state == "ready" and voice.warmed and voice.calls == 1
    await service.release()
    assert not voice.warmed


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["game_mode", "live", "voice_replies"])
async def test_disabled_or_paused_settings_release_and_resume_warms_again(shared, change):
    service, voice, cfg, root, released = shared
    await service.tick()
    await service.task
    await service.tick()
    cfg[change] = change in {"game_mode", "paused"}
    await service.tick()
    assert not voice.warmed and service.state in {"game_mode", "paused", "disabled"}
    assert released == [dict(tts_only=True)]
    cfg[change] = not cfg[change]
    await service.tick()
    await service.task
    await service.tick()
    assert voice.warmed and voice.calls == 2
    await service.release()


@pytest.mark.asyncio
async def test_pause_keeps_already_warm_voice_and_resume_does_not_cold_start(shared):
    service, voice, cfg, root, released = shared
    await service.tick()
    await service.task
    await service.tick()
    cfg["paused"] = True
    await service.tick()
    assert voice.warmed and service.state == "paused" and not released
    cfg["paused"] = False
    await service.tick()
    assert service.state == "ready" and voice.calls == 1
    await service.release()


@pytest.mark.asyncio
async def test_starting_paused_does_not_load_voice(shared):
    service, voice, cfg, root, released = shared
    cfg["paused"] = True
    await service.tick()
    assert service.state == "paused" and not voice.warmed and voice.calls == 0


@pytest.mark.asyncio
async def test_song_preparation_borrows_gpu_then_restores_voice(shared, monkeypatch):
    service, voice, cfg, root, released = shared
    await service.tick()
    await service.task
    await service.tick()
    singing = [True]
    monkeypatch.setattr(voice_service, "active_singing_job", lambda: {"song_id": "test"} if singing[0] else None)
    await service.tick()
    assert service.state == "singing" and not voice.warmed and released
    singing[0] = False
    await service.tick()
    await service.task
    await service.tick()
    assert service.state == "ready" and voice.calls == 2
    await service.release()


@pytest.mark.asyncio
async def test_failed_warmup_degrades_without_sticking_or_retrying_every_tick(shared, monkeypatch):
    service, voice, cfg, root, _ = shared
    monkeypatch.setattr(voice_service, "WARM_TIMEOUT", 0.01)

    async def stuck():
        voice.calls += 1
        await asyncio.Event().wait()

    voice.prewarm = stuck
    await service.tick()
    await service.task
    await service.tick()
    assert service.state == "unavailable" and not voice.warmed
    await service.tick()
    assert voice.calls == 1
    await service.release()


@pytest.mark.asyncio
async def test_dead_owned_process_reenters_preparation(shared):
    service, voice, cfg, root, _ = shared
    await service.tick()
    await service.task
    await service.tick()
    voice.process = SimpleNamespace(poll=lambda: 1)
    await service.tick()
    assert service.state == "warming" and not voice.warmed
    await service.release()


def test_stale_or_other_process_heartbeat_is_never_voice_ready(shared):
    service, voice, cfg, root, _ = shared
    path = root / "data/voice-service.json"
    path.write_text(json.dumps(dict(state="ready", pid=12, updated=time.time())), encoding="utf-8")
    assert not voice_status.read_voice_status(cfg, SimpleNamespace(pid=13))["ready"]
    assert voice_status.read_voice_status(cfg, SimpleNamespace(pid=12))["ready"]
    path.write_text(json.dumps(dict(state="ready", pid=12, updated=time.time() - 20)), encoding="utf-8")
    assert not voice_status.read_voice_status(cfg, SimpleNamespace(pid=12))["ready"]


@pytest.mark.asyncio
async def test_managed_chat_waits_for_shared_readiness_and_reuses_warm_service(voice_engine, monkeypatch):
    engine, store, cfg, sends, file = voice_engine
    engine.voice.managed = True
    ready = [False]
    monkeypatch.setattr(voice_status, "read_voice_status", lambda cfg: dict(ready=ready[0]))
    result = await engine.decision("12345", "reply")
    assert not (await engine.voice.prepare("12345", result)).get("voice_file")
    ready[0] = True
    assert (await engine.voice.prepare("12345", result))["voice_file"] == str(file)


def test_starting_bot_starts_shared_voice_first_and_reuses_existing_service(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(services, "ROOT", tmp_path)
    running, started = {}, []
    monkeypatch.setattr(services, "matching", lambda: running)

    def spawn(args, **kwargs):
        name = "voice" if args[1].endswith("voice_service.py") else "bot"
        started.append(name)
        running[name] = object()

    monkeypatch.setattr(services.subprocess, "Popen", spawn)
    services.start(["bot"])
    services.start(["bot"])
    assert started == ["voice", "bot"]
