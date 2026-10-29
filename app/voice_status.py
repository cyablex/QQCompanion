"""Read the shared voice service heartbeat without starting inference."""

import json
import time

from settings import ROOT


def voice_required(cfg):
    return bool(cfg.get("voice_replies") and cfg.get("live") and not cfg.get("paused") and not cfg.get("game_mode"))


def game_release_message():
    try:
        release = json.loads((ROOT / "data/game-mode-release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        release = {}
    if release.get("denied"):
        return "游戏模式中，部分工作进程未能释放，请检查游戏模式状态。"
    return "游戏模式中，语音已暂停；工作进程释放结果见游戏模式状态。"


def read_voice_status(cfg, process=None, now=None):
    if not voice_required(cfg):
        state = "game_mode" if cfg.get("game_mode") else "paused" if cfg.get("paused") else "disabled"
        return dict(state=state, ready=False, message=game_release_message() if state == "game_mode" else "语音当前已暂停。")
    now = time.time() if now is None else now
    try:
        data = json.loads((ROOT / "data/voice-service.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if process is None:
        from services import matching

        process = matching().get("voice")
    if (process is None or data.get("pid") != getattr(process, "pid", None)
            or not 0 <= now - data.get("updated", 0) < 15):
        return dict(state="starting", ready=False, message="正在启动语音服务。")
    state = data.get("state", "warming")
    return dict(state=state, ready=state == "ready", message=data.get("message", "正在准备声音。"),
                seconds=data.get("seconds"), since=data.get("since"))
