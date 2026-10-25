"""One local Windows notice per sustained outage, followed by recovery."""

import json
import subprocess
from pathlib import Path

from settings import ROOT, atomic_json

REASONS = {
    "waiting_login": "需要扫码或手机确认登录。",
    "wrong_account": "当前登录的 QQ 账号不匹配。",
    "session_unavailable": "QQ 会话异常，暂时不能正常聊天。后台会尝试恢复。",
    "recovery_failed": "自动恢复次数已用完，请检查网络和登录页面，必要时重新启动。",
    "bot_stopped": "聊天程序未运行，后台正在尝试恢复。",
    "status_unavailable": "状态检测没有更新，暂时无法确认是否在线。",
}


def windows_notice(title, message):
    """Use the user's desktop notification area; no QQ or model API involved."""
    with (ROOT / "logs/connection-notices.log").open("ab") as log:
        return subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                "-File", str(ROOT / "app/connection_notice.ps1"),
                "-Title", title, "-Message", message,
            ],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def connection_phase(startup, health, bot_running, now):
    if not bot_running:
        return "bot_stopped"
    if not 0 <= now - startup.get("updated", 0) < 45:
        return "status_unavailable"
    phase = startup.get("phase", "starting")
    if phase == "ready" and not (
        health.get("transport_ready") is True
        and 0 <= now - health.get("updated", 0) < 90
    ):
        return "status_unavailable"
    return phase


class ConnectionAlerts:
    def __init__(self, path=None, notify=windows_notice):
        self.path = Path(path) if path else ROOT / "data/connection-alerts.json"
        self.notify = notify
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}

    def tick(self, phase, now, enabled=True):
        state = self.state.copy()
        if not enabled or phase == "paused":
            state = {"seen_ready": state.get("seen_ready", False)}
        elif phase == "ready":
            if state.get("notified"):
                self.notify("大肥鱼已恢复上线", "QQ 聊天连接已恢复，可以继续聊天了。")
            state = {"seen_ready": True}
        else:
            state.setdefault("since", now)
            state["phase"] = phase
            delay = 60 if state.get("seen_ready") else 90
            if not state.get("notified") and now - state["since"] >= delay:
                reason = REASONS.get(phase, "QQ 连接持续未就绪，暂时不能确认在线。")
                self.notify("大肥鱼连接异常", reason + " 点击通知打开启动页面。")
                state["notified"] = True
                state["notified_at"] = now
                state["recovery_failed_notified"] = phase == "recovery_failed"
            # A separate reminder is useful when automatic recovery gives up,
            # after the initial outage notice, rather than repeating each tick.
            elif state.get("notified") and phase == "recovery_failed" and not state.get("recovery_failed_notified"):
                self.notify("大肥鱼需要你处理", REASONS[phase] + " 点击打开启动页面。")
                state["recovery_failed_notified"] = True
        if state != self.state:
            atomic_json(self.path, state)
            self.state = state

