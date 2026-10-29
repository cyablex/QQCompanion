"""Simulated process snapshots; no real games or unrelated processes are stopped."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import game_mode  # noqa: E402
import settings  # noqa: E402
import vpet_runtime  # noqa: E402
from engine import Engine  # noqa: E402
from store import Store  # noqa: E402


@pytest.fixture
def detector(tmp_path, monkeypatch):
    for module in (settings, game_mode):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(game_mode, "TTS_ROOT", tmp_path.parent / "GPTSoVITS")
    cfg = settings.read_settings()
    cfg.update(game_mode_auto=True, voice_replies=True, voice_understanding=True,
               singing_enabled=True, live=True)
    settings.atomic_json(tmp_path / "config/user.json", cfg)
    games, stops, restores = [], [], []
    monkeypatch.setattr(game_mode.psutil, "process_iter", lambda attrs: [
        SimpleNamespace(info=dict(name=name)) for name in games])
    monkeypatch.setattr(game_mode, "close_local_voice", lambda: stops.append(True) or dict(stopped=[101], denied=[]))
    monkeypatch.setattr(vpet_runtime, "stop", lambda root: dict(stopped=[102], denied=[]))
    monkeypatch.setattr(vpet_runtime, "reconcile", lambda root, cfg: restores.append(dict(cfg)) or dict(stopped=[], denied=[]))
    return game_mode.GameMonitor(), games, stops, restores


@pytest.mark.parametrize("game", ["Endfield.exe", "VALORANT-Win64-Shipping.exe"])
@pytest.mark.parametrize("already_running", [False, True])
def test_game_start_exit_and_no_repeated_switches(detector, game, already_running):
    monitor, games, stops, restores = detector
    if not already_running:
        monitor.tick()
        assert not settings.read_settings()["game_mode"]
    games.append(game)
    assert monitor.tick() == dict(stopped=[101, 102], denied=[])
    active = settings.read_settings()
    assert active["game_mode"] and not active["game_mode_manual"]
    for _ in range(4):
        monitor.tick()
    assert settings.read_settings() == active and stops == [True] and not restores
    games.clear()
    monitor.tick()
    cfg = settings.read_settings()
    assert not cfg["game_mode"] and len(restores) == 1
    assert cfg["voice_lazy"] and all(cfg[k] for k in (
        "voice_replies", "voice_understanding", "singing_enabled", "live"))
    monitor.tick()
    assert len(restores) == 1


def test_both_games_must_exit(detector):
    monitor, games, stops, restores = detector
    games.extend(["Endfield.exe", "VALORANT-Win64-Shipping.exe"])
    monitor.tick()
    games.pop(0)
    monitor.tick()
    assert settings.read_settings()["game_mode"] and not restores
    games.clear()
    monitor.tick()
    assert not settings.read_settings()["game_mode"] and len(restores) == 1 and stops == [True]


@pytest.mark.parametrize("before_game", [True, False])
def test_manual_on_survives_game_exit_and_auto_off(detector, before_game):
    monitor, games, stops, restores = detector
    if before_game:
        game_mode.set_game_mode(True)
    games.append("Endfield.exe")
    monitor.tick()
    if not before_game:
        game_mode.set_game_mode(True)
    games.clear()
    monitor.tick()
    game_mode.set_auto_detection(False)
    monitor.tick()
    assert settings.read_settings()["game_mode_manual"]
    assert settings.read_settings()["game_mode"] and not restores


def test_manual_off_respected_across_restart_and_second_game_until_all_exit(detector):
    monitor, games, stops, restores = detector
    games.append("Endfield.exe")
    monitor.tick()
    game_mode.set_game_mode(False)
    games.append("VALORANT-Win64-Shipping.exe")
    monitor = game_mode.GameMonitor()
    monitor.tick()
    games.pop(0)
    monitor.tick()
    assert not settings.read_settings()["game_mode"] and stops == [True]
    assert settings.read_settings()["game_mode_override"]
    games.clear()
    monitor.tick()
    assert not settings.read_settings()["game_mode_override"]
    games.append("Endfield.exe")
    monitor.tick()
    assert settings.read_settings()["game_mode"] and stops == [True, True]


def test_auto_off_restores_only_automatically_owned_mode(detector):
    monitor, games, stops, restores = detector
    games.append("Endfield.exe")
    monitor.tick()
    game_mode.set_auto_detection(False)
    monitor.tick()
    assert not settings.read_settings()["game_mode"] and len(stops) == len(restores) == 1
    game_mode.set_auto_detection(True)
    monitor.tick()
    assert settings.read_settings()["game_mode"] and len(stops) == 2


def test_manual_off_without_game_does_not_suppress_next_launch(detector):
    monitor, games, stops, restores = detector
    game_mode.set_game_mode(False)
    games.append("Endfield.exe")
    monitor.tick()
    assert settings.read_settings()["game_mode"] and stops == [True]


def test_legacy_manual_mode_migrates_without_being_cleared(detector):
    monitor, games, stops, restores = detector
    cfg = settings.read_settings()
    cfg["game_mode"] = True
    settings.atomic_json(game_mode.ROOT / "config/user.json", cfg)
    monitor.tick()
    assert settings.read_settings()["game_mode_manual"] and not restores


def test_failed_detection_preserves_state_and_reports_error(detector, monkeypatch):
    monitor, games, stops, restores = detector
    games.append("Endfield.exe")
    monitor.tick()
    cfg = settings.read_settings()

    def denied(attrs):
        raise game_mode.psutil.AccessDenied(42)

    monkeypatch.setattr(game_mode.psutil, "process_iter", denied)
    monitor.tick_safely()
    assert settings.read_settings() == cfg and not restores
    assert game_mode.status()["monitor"]["error"] == "AccessDenied"


def test_polling_uses_four_second_wait_and_ignores_similar_names(detector):
    monitor, games, stops, restores = detector
    games.extend(["NotEndfield.exe", "Endfield.exe.bak", "VALORANT.exe", "python.exe"])
    intervals = []
    stop = SimpleNamespace(is_set=lambda: bool(intervals), wait=lambda seconds: intervals.append(seconds))
    monitor.run(stop)
    assert intervals == [4] and not settings.read_settings()["game_mode"] and not stops


def test_auto_commands_require_owner_and_do_not_change_manual_state(detector):
    cfg = settings.read_settings()
    cfg["owner_qq"] = "11111"
    settings.atomic_json(game_mode.ROOT / "config/user.json", cfg)
    store = Store(game_mode.ROOT / "commands.sqlite3")
    try:
        engine = Engine(store, None, None, settings.read_settings)
        assert engine.admin("33333", "/游戏模式 自动 关") is None
        assert settings.read_settings()["game_mode_auto"]
        assert "已关闭" in engine.admin("11111", "/游戏模式 自动 关")
        assert not settings.read_settings()["game_mode_auto"]
        assert "已开启" in engine.admin("11111", "/游戏模式 自动 开")
        assert settings.read_settings()["game_mode_auto"]
    finally:
        store.db.close()


@pytest.mark.parametrize("failure", ["denied", "timeout", "kill_success"])
def test_release_waits_for_children_and_reports_failure_without_false_success(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(game_mode, "ROOT", tmp_path)
    stopped = []

    class Worker:
        def __init__(self, pid):
            self.pid = pid
            self.info = dict(exe=str(tmp_path / "runtime/python.exe"), cmdline=[str(tmp_path / "app/speech_worker.py")])
            self.killed = False

        def children(self, recursive):
            return [child] if self.pid == 1 else []

        def terminate(self):
            if self.pid == 2 and failure == "denied":
                raise game_mode.psutil.AccessDenied(self.pid)

        def wait(self, timeout):
            if self.pid == 2 and failure != "denied" and (not self.killed or failure == "timeout"):
                raise game_mode.psutil.TimeoutExpired(timeout, self.pid)
            stopped.append(self.pid)

        def kill(self):
            self.killed = True

    parent, child = Worker(1), Worker(2)
    monkeypatch.setattr(game_mode.psutil, "process_iter", lambda attrs: [parent])
    result = game_mode.close_local_voice()
    if failure == "kill_success":
        assert result == dict(stopped=[2, 1], denied=[]) and child.killed
    else:
        assert result == dict(stopped=[1], denied=[2]) and 2 not in stopped


def test_denied_release_is_visible_in_status(detector, monkeypatch):
    monitor, games, stops, restores = detector
    monkeypatch.setattr(game_mode, "close_local_voice", lambda: dict(stopped=[], denied=[42]))
    games.append("Endfield.exe")
    assert monitor.tick()["denied"] == [42]
    assert game_mode.status()["release"]["denied"] == [42]
    assert json.loads((game_mode.ROOT / "data/game-mode-release.json").read_text())["denied"] == [42]
