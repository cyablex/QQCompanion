import asyncio
import base64
import json
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import game_mode
import media
from engine import Engine
from game_mode import VoicePaused
from media import LocalSpeech, MediaPipeline
from store import Store


@pytest.mark.parametrize("tts_only", [False, True])
def test_switch_closes_only_owned_voice_inference_processes(tmp_path, monkeypatch, tts_only):
    root = tmp_path / "QQCompanion"
    tts = tmp_path / "GPTSoVITS"
    monkeypatch.setattr(game_mode, "ROOT", root)
    monkeypatch.setattr(game_mode, "TTS_ROOT", tts)
    stopped = []

    class Process:
        def __init__(self, pid, executable, arguments):
            self.pid = pid
            self.info = dict(pid=pid, exe=str(executable), cmdline=list(map(str, arguments)))

        def terminate(self):
            stopped.append(self.pid)

        def wait(self, timeout):
            return 0

        def children(self, recursive=True):
            return []

        def cwd(self):
            return tmp_path / ("SeedVC" if self.pid in {8, 10} else "other")

    processes = [
        Process(1, root / "runtime/python.exe", [root / "app/speech_worker.py"]),
        Process(2, tts / "app/bundle/runtime/python.exe", [tts / "app/bundle/api_v2.py"]),
        Process(3, root / "runtime/python.exe", [root / "app/run_astrbot.py"]),
        Process(4, root / "QQ.exe", []),
        Process(5, tmp_path / "VPN.exe", []),
        Process(6, tmp_path / "python.exe", ["api_v2.py"]),
        Process(7, tmp_path / "pwsh.exe", ["contains " + str(root / "app/speech_worker.py")]),
        Process(8, tmp_path / "SeedVC/.venv/Scripts/python.exe", [root / "integrations/seedvc/companion_svc.py"]),
        Process(9, tmp_path / "python.exe", ["companion_svc.py"]),
        Process(10, tmp_path / "SeedVC/.venv/Scripts/python.exe", [root / "app/song_worker.py", "--song-id", "demo"]),
        Process(11, tmp_path / "python.exe", [tmp_path / "other/song_worker.py"]),
        Process(12, tts / "app/bundle/runtime/python.exe", [tmp_path / "other/api_v2.py"]),
    ]
    monkeypatch.setattr(game_mode.psutil, "process_iter", lambda attrs: processes)
    expected = [2] if tts_only else [1, 2, 8, 10]
    assert game_mode.close_local_voice(tts_only=tts_only) == dict(stopped=expected, denied=[])
    assert stopped == expected


def test_switch_is_persistent_and_off_does_not_restart_voice(tmp_path, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(game_mode, "ROOT", tmp_path)
    stopped = []
    monkeypatch.setattr(game_mode, "close_local_voice", lambda: stopped.append(True) or dict(stopped=[], denied=[]))
    cfg = dict(revision=5, voice_understanding=True, live=True, game_mode=False)
    game_mode.set_game_mode(True, cfg)
    saved = json.loads((tmp_path / "config/user.json").read_text(encoding="utf-8"))
    assert saved["game_mode"] and saved["voice_understanding"] and saved["live"]
    game_mode.set_game_mode(False, saved)
    saved = json.loads((tmp_path / "config/user.json").read_text(encoding="utf-8"))
    assert not saved["game_mode"] and saved["revision"] == 7
    assert stopped == [True]


@pytest.mark.asyncio
async def test_game_mode_prevents_worker_start_and_media_download(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "read_settings", lambda: dict(game_mode=True))

    async def forbidden(*args, **kwargs):
        raise AssertionError("Game mode must not start or download voice work")

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", forbidden)
    with pytest.raises(VoicePaused):
        await LocalSpeech().transcribe(b"voice")
    store = Store(tmp_path / "media.sqlite3")
    pipe = MediaPipeline(SimpleNamespace(store=store))
    pipe.resolve = forbidden
    store.incoming("voice-event", "33333", "[语音]")
    pipe.capture("voice-event", "33333", "[语音]", [("voice", {"file": "opaque"})])
    await pipe.prepare("33333")
    row = store.db.execute("SELECT state,summary FROM media_inputs").fetchone()
    assert row["state"] == "skipped_game" and "游戏模式" in row["summary"]
    assert "改发文字" in store.history("33333")[-1]["content"]
    store.db.close()


@pytest.mark.asyncio
async def test_voice_worker_exits_after_response_without_parent_model_residency(monkeypatch):
    monkeypatch.setattr(media, "read_settings", lambda: dict(game_mode=False))

    class Worker:
        returncode = None

        async def communicate(self, data):
            assert base64.b64decode(data) == b"wave"
            self.returncode = 0
            return json.dumps(dict(text="你好呀"), ensure_ascii=False).encode("utf-8"), b""

    async def spawn(*args, **kwargs):
        assert args[1].endswith("speech_worker.py")
        return Worker()

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", spawn)
    speech = LocalSpeech()
    assert await speech.transcribe(b"wave") == "你好呀"
    assert speech.recognizer is None


@pytest.mark.asyncio
async def test_cancelled_voice_processing_terminates_worker(monkeypatch):
    monkeypatch.setattr(media, "read_settings", lambda: dict(game_mode=False))
    started = asyncio.Event()
    killed = []

    class Worker:
        returncode = None

        async def communicate(self, data):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            killed.append(True)
            self.returncode = -1

        async def wait(self):
            return self.returncode

    async def spawn(*args, **kwargs):
        return Worker()

    monkeypatch.setattr(media.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(LocalSpeech().transcribe(b"wave"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert killed == [True]


def test_game_mode_command_requires_transport_owner(tmp_path, monkeypatch):
    store = Store(tmp_path / "commands.sqlite3")
    cfg = dict(owner_qq="11111", revision=1)
    changed = []
    monkeypatch.setattr(game_mode, "set_game_mode", lambda enabled, settings: changed.append(enabled) or dict(stopped=[], denied=[]))
    engine = Engine(store, None, None, lambda: dict(cfg))
    assert engine.admin("33333", "/游戏模式 开") is None
    assert not changed
    assert "已开启" in engine.admin("11111", "/游戏模式 开")
    assert "已关闭" in engine.admin("11111", "/游戏模式 关")
    assert changed == [True, False]
    store.db.close()


def load_tts_launcher():
    path = Path(__file__).resolve().parents[1] / "integrations/gptsovits/launch.py"
    spec = importlib.util.spec_from_file_location("test_local_tts_launcher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tts_launcher_refuses_to_start_during_game_mode(tmp_path, monkeypatch):
    launcher = load_tts_launcher()
    monkeypatch.setattr(launcher, "ROOT", tmp_path / "GPTSoVITS")
    monkeypatch.setenv("QQCOMPANION_ROOT", str(tmp_path / "QQCompanion"))
    settings = tmp_path / "QQCompanion/config/user.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"game_mode":true}', encoding="utf-8")

    def forbidden():
        raise AssertionError("Must not load the GPU environment")

    monkeypatch.setattr(launcher, "configure_environment", forbidden)
    with pytest.raises(SystemExit, match="Game mode is on"):
        launcher.main()


def test_tts_launcher_ends_its_own_child_when_game_mode_changes(tmp_path, monkeypatch):
    launcher = load_tts_launcher()
    monkeypatch.setattr(launcher, "ROOT", tmp_path / "GPTSoVITS")
    monkeypatch.setenv("QQCOMPANION_ROOT", str(tmp_path / "QQCompanion"))
    settings = tmp_path / "QQCompanion/config/user.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"game_mode":false}', encoding="utf-8")
    monkeypatch.setattr(launcher, "configure_environment", lambda: None)
    monkeypatch.setattr(launcher.sys, "argv", ["launch.py", "api"])
    stopped = []

    class Child:
        def wait(self, timeout):
            if not stopped:
                settings.write_text('{"game_mode":true}', encoding="utf-8")
                raise launcher.subprocess.TimeoutExpired("fake-tts", timeout)
            return 0

        def poll(self):
            return None

        def terminate(self):
            stopped.append(True)

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: Child())
    launcher.main()
    assert stopped == [True]


def test_settings_page_game_switch_saves_and_closes_voice(tmp_path, monkeypatch):
    import io
    from urllib.parse import urlencode
    import settings

    monkeypatch.setattr(settings, "ROOT", tmp_path)
    monkeypatch.setattr(game_mode, "ROOT", tmp_path)
    (tmp_path / "config").mkdir()
    path = Path(__file__).resolve().parents[1] / "app/setup_server.py"
    spec = importlib.util.spec_from_file_location("test_setup_game_mode", path)
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    cfg = settings.read_settings()
    cfg.update(owner_qq="11111", bot_qq="22222", name="大肥鱼", persona="自然")
    monkeypatch.setattr(server, "read_settings", lambda: dict(cfg))
    closed = []
    monkeypatch.setattr(game_mode, "close_local_voice", lambda: closed.append(True) or dict(stopped=[], denied=[]))
    handler = server.Handler.__new__(server.Handler)
    handler.allowed_host = lambda: True
    rendered = []
    handler.response = lambda body, *args, **kwargs: rendered.append(body)
    handler.path = "/"
    handler.do_GET()
    assert 'name="game_mode"' in rendered[-1]
    fields = {k:cfg[k] for k in ("owner_qq","bot_qq","name","owner_title","persona","model")}
    fields.update(csrf=server.CSRF, game_mode="on")
    body = urlencode(fields).encode("utf-8")
    handler.path = "/save"
    handler.headers = {"Content-Type":"application/x-www-form-urlencoded","Content-Length":str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler.do_POST()
    saved = json.loads((tmp_path / "config/user.json").read_text(encoding="utf-8"))
    assert saved["game_mode"] and closed == [True]
