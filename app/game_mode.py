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

from settings import ROOT, atomic_json, read_settings

TTS_ROOT = ROOT.parent / "GPTSoVITS"
GAMES = {"endfield.exe", "valorant-win64-shipping.exe"}
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
    for process in psutil.process_iter(["pid", "exe", "cmdline"]):
        selected = False
        try:
            executable = Path(process.info["exe"] or "").resolve()
            args = process.info["cmdline"] or []
            asr = executable == runtime and str(worker).casefold() in [str(arg).casefold() for arg in args]
            tts = (executable.is_relative_to((TTS_ROOT / "app").resolve())
                   and executable.name.lower() in {"python.exe", "pythonw.exe"}
                   and any(Path(arg).is_absolute() and Path(arg).resolve().is_relative_to((TTS_ROOT / "app").resolve())
                           and Path(arg).name.lower() in {"api_v2.py", "inference_webui.py"} for arg in args))
            svc = (executable == (ROOT.parent / "SeedVC/.venv/Scripts/python.exe").resolve()
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
    if enabled and (not previous.get("game_mode") or enforce):
        cfg.update(voice_lazy=True, voice_demand_after=time.time())
    if cfg != previous:
        cfg["revision"] = previous.get("revision", 0) + 1
        atomic_json(ROOT / "config/user.json", cfg)
    import vpet_runtime
    if enabled and (not previous.get("game_mode") or enforce):
        outcome = close_local_voice()
        pet = vpet_runtime.stop(ROOT)
        outcome = dict(stopped=outcome['stopped'] + pet['stopped'], denied=outcome['denied'] + pet['denied'])
        atomic_json(ROOT / "data/game-mode-release.json", dict(updated=time.time(), **outcome))
        return outcome
    if previous.get("game_mode") and not enabled:
        return vpet_runtime.reconcile(ROOT, cfg)
    return dict(stopped=[], denied=[])


def set_game_mode(enabled, cfg=None):
    with mode_lock():
        previous = dict(read_settings() if cfg is None or (ROOT / "config/user.json").exists() else cfg)
        override = False
        if not enabled and previous.get("game_mode_auto"):
            try:
                override = bool(running_games())
            except psutil.Error:
                override = True
        cfg = dict(previous, game_mode=bool(enabled), game_mode_manual=bool(enabled),
                   game_mode_override=override)
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
    found = set()
    for process in psutil.process_iter(["name"]):
        try:
            name = process.info["name"]
            if name is None:
                name = process.name()  # Failure is unknown, never a false game exit.
            if name.casefold() in GAMES:
                found.add(name.casefold())
        except psutil.NoSuchProcess:
            continue
    return found


class GameMonitor:
    def __init__(self):
        self.first = True

    def tick(self):
        with mode_lock():
            previous = read_settings()
            games = running_games() if previous.get("game_mode_auto") else set()
            active = bool(games)
            manual = previous.get("game_mode_manual", bool(previous.get("game_mode")))
            override = bool(previous.get("game_mode_override")) and active
            cfg = dict(previous, game_mode_manual=manual, game_mode_auto_active=active,
                       game_mode_override=override, game_mode=bool(manual or (active and not override)))
            outcome = _apply(cfg, previous, enforce=self.first and cfg["game_mode"])
            self.first = False
            atomic_json(ROOT / "data/game-mode-monitor.json", dict(
                updated=time.time(), games=sorted(games), enabled=bool(cfg.get("game_mode_auto")),
                game_mode=cfg["game_mode"], manual=manual, override=override, error=""))
            return outcome

    def tick_safely(self):
        try:
            return self.tick()
        except (OSError, ValueError, psutil.Error, TimeoutError) as exc:
            atomic_json(ROOT / "data/game-mode-monitor.json", dict(
                updated=time.time(), error=type(exc).__name__))

    def run(self, stop):
        while not stop.is_set():
            self.tick_safely()
            stop.wait(4)


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
