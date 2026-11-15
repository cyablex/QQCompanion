"""Manual and process-driven resource control for this installation."""

import argparse
import ctypes
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import psutil

from process_snapshot import named_pids, named_processes
from settings import ROOT, atomic_json, read_settings

TTS_ROOT = ROOT.parent / "GPTSoVITS"
GAMES = {"endfield.exe", "valorant-win64-shipping.exe"}
CHECK_INTERVAL = 4
_lock = threading.RLock()


@contextmanager
def mode_lock():
    """Serialize detector and commands across this installation's processes."""
    with _lock:
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.restype = ctypes.c_void_p
            tag = str(ROOT.resolve()).replace("\\", "_").replace("/", "_").replace(":", "_")
            handle = kernel.CreateMutexW(None, False, "Local\\QQCompanionGame_" + tag)
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if kernel.WaitForSingleObject(ctypes.c_void_p(handle), 30000) not in {0, 128}:
                    raise TimeoutError("Game mode is busy")
                try:
                    yield
                finally:
                    kernel.ReleaseMutex(ctypes.c_void_p(handle))
            finally:
                kernel.CloseHandle(ctypes.c_void_p(handle))
        else:
            import fcntl
            path = ROOT / "data/game-mode.lock"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a") as file:
                fcntl.flock(file, fcntl.LOCK_EX)
                yield


def stop_process(process):
    """Called only for an identified project worker or its captured children."""
    process.terminate()
    try:
        process.wait(timeout=2)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


class VoicePaused(RuntimeError):
    pass


def close_local_voice(tts_only=False):
    stopped = []
    denied = []
    worker = (ROOT / "app/speech_worker.py").resolve()
    runtime = (ROOT / "runtime/python.exe").resolve()
    tts_dir = (TTS_ROOT / "app").resolve()
    seed_runtime = (ROOT.parent / "SeedVC/.venv/Scripts/python.exe").resolve()
    for process in named_processes({"python.exe", "pythonw.exe"}):
        selected = False
        try:
            executable = Path(process.exe()).resolve()
            if executable not in {runtime, seed_runtime} and not executable.is_relative_to(tts_dir):
                continue
            args = process.cmdline()
            asr = executable == runtime and str(worker).casefold() in [str(arg).casefold() for arg in args]
            tts = (executable.is_relative_to(tts_dir)
                   and executable.name.lower() in {"python.exe", "pythonw.exe"}
                   and any(Path(arg).is_absolute() and Path(arg).resolve().is_relative_to(tts_dir)
                           and Path(arg).name.lower() in {"api_v2.py", "inference_webui.py"} for arg in args))
            svc = (executable == seed_runtime
                   and any(Path(arg).resolve() in {(ROOT / "integrations/seedvc/companion_svc.py").resolve(), (ROOT.parent / "SeedVC/companion_svc.py").resolve(), (ROOT / "app/song_worker.py").resolve()} for arg in args)
                   and Path(process.cwd()).resolve() == (ROOT.parent / "SeedVC").resolve())
            if tts_only:
                asr = svc = False
                tts = tts and any(Path(arg).name.lower() == "api_v2.py" for arg in args)
            if not (asr or tts or svc):
                continue
            selected = True
            # Snapshot children before stopping their parent (ffmpeg/conversion).
            for child in reversed(process.children(recursive=True)):
                try:
                    stop_process(child)
                    stopped.append(child.pid)
                except psutil.NoSuchProcess:
                    pass
                except (psutil.AccessDenied, psutil.TimeoutExpired):
                    denied.append(child.pid)
            stop_process(process)
            stopped.append(process.pid)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, psutil.TimeoutExpired):
            if selected:
                denied.append(process.pid)
    return dict(stopped=stopped, denied=denied)


def _apply(cfg, previous, *, enforce=False):
    enabled = cfg["game_mode"]
    was_enabled = bool(previous.get("game_mode"))
    if enabled and (not was_enabled or enforce):
        cfg.update(voice_lazy=True, voice_demand_after=time.time())
    if cfg != previous:
        if enabled != was_enabled or enforce or cfg.get("game_mode_auto") != previous.get("game_mode_auto"):
            cfg["revision"] = previous.get("revision", 0) + 1
        atomic_json(ROOT / "config/user.json", cfg)
    import vpet_runtime
    if enabled and (not was_enabled or enforce):
        outcome = close_local_voice()
        pet = vpet_runtime.stop(ROOT)
        outcome = dict(stopped=outcome['stopped'] + pet['stopped'], denied=outcome['denied'] + pet['denied'])
        atomic_json(ROOT / "data/game-mode-release.json", dict(updated=time.time(), **outcome))
        return outcome
    if was_enabled and not enabled:
        return vpet_runtime.reconcile(ROOT, cfg)
    return dict(stopped=[], denied=[])


def set_game_mode(enabled, cfg=None):
    with mode_lock():
        previous = dict(read_settings() if cfg is None or (ROOT / "config/user.json").exists() else cfg)
        active = detection_failed = False
        if previous.get("game_mode_auto"):
            try:
                active = bool(running_games())
            except (OSError, psutil.Error):
                active = bool(previous.get("game_mode_auto_active"))
                detection_failed = True
        cfg = dict(previous, game_mode=bool(enabled), game_mode_manual=bool(enabled and not active),
                   game_mode_auto_active=active, game_mode_override=bool(not enabled and (active or detection_failed)))
        return _apply(cfg, previous, enforce=bool(enabled))


def set_auto_detection(enabled):
    with mode_lock():
        previous = read_settings()
        manual = previous.get("game_mode_manual", bool(previous.get("game_mode")))
        cfg = dict(previous, game_mode_auto=bool(enabled), game_mode_manual=manual,
                   game_mode_override=False)
        if not enabled:
            cfg.update(game_mode_auto_active=False, game_mode=manual)
        return _apply(cfg, previous)


def running_games():
    return {name for _, name in named_pids(GAMES)}


class GameMonitor:
    def __init__(self):
        self.first = True
        self.last_status = None

    def report(self, **status):
        if status != self.last_status:
            atomic_json(ROOT / "data/game-mode-monitor.json", dict(updated=time.time(), **status))
            self.last_status = status

    def tick(self):
        with mode_lock():
            previous = read_settings()
            auto = bool(previous.get("game_mode_auto"))
            games = running_games() if auto else set()
            active = bool(games)
            manual = previous.get("game_mode_manual", bool(previous.get("game_mode")))
            # An early manual start hands over to the detected game session.
            if auto and (active or previous.get("game_mode_auto_active")):
                manual = False
            override = bool(previous.get("game_mode_override")) and active
            cfg = dict(previous, game_mode_manual=manual, game_mode_auto_active=active,
                       game_mode_override=override, game_mode=bool(manual or (active and not override)))
            outcome = _apply(cfg, previous, enforce=self.first and cfg["game_mode"])
            self.first = False
            self.report(games=sorted(games), enabled=auto, game_mode=cfg["game_mode"],
                        manual=manual, override=override, error="")
            return outcome

    def tick_safely(self):
        try:
            return self.tick()
        except (OSError, ValueError, psutil.Error, TimeoutError) as exc:
            self.report(error=type(exc).__name__)

    def run(self, stop):
        while not stop.is_set():
            self.tick_safely()
            stop.wait(CHECK_INTERVAL)


def status():
    result = {}
    for name in ("monitor", "release"):
        try:
            result[name] = json.loads((ROOT / f"data/game-mode-{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result[name] = {}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["on", "off", "auto-on", "auto-off"])
    selected = parser.parse_args().mode
    outcome = set_auto_detection(selected == "auto-on") if selected.startswith("auto-") else set_game_mode(selected == "on")
    print("Game mode:", selected, outcome)
