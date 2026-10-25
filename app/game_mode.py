"""Manual resource control for this installation's local voice inference."""

import argparse
from pathlib import Path

import psutil

from settings import ROOT, atomic_json, read_settings

TTS_ROOT = ROOT.parent / "GPTSoVITS"


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
            tts = executable.is_relative_to((TTS_ROOT / "app").resolve()) and executable.name.lower() in {"python.exe", "pythonw.exe"} and any(Path(arg).name.lower() in {"api_v2.py", "inference_webui.py"} for arg in args)
            svc = (executable.name.lower() in {"python.exe", "pythonw.exe"}
                   and any(Path(arg).name.lower() == "companion_svc.py" or Path(arg).resolve() == (ROOT / "app/song_worker.py").resolve() for arg in args)
                   and Path(process.cwd()).resolve() == (ROOT.parent / "SeedVC").resolve())
            if tts_only:
                asr = svc = False
                tts = tts and any(Path(arg).name.lower() == "api_v2.py" for arg in args)
            if not (asr or tts or svc):
                continue
            selected = True
            process.terminate()
            try:
                process.wait(timeout=2)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            stopped.append(process.pid)
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            if selected:
                denied.append(process.pid)
    return dict(stopped=stopped, denied=denied)


def set_game_mode(enabled, cfg=None):
    cfg = dict(read_settings() if cfg is None else cfg)
    cfg.update(game_mode=bool(enabled), revision=cfg["revision"] + 1)
    atomic_json(ROOT / "config/user.json", cfg)
    return close_local_voice() if enabled else dict(stopped=[], denied=[])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["on", "off"])
    selected = parser.parse_args().mode
    outcome = set_game_mode(selected == "on")
    print("Game mode:", selected, outcome)
