"""Watch QQ readiness and open one login page when phone approval is needed."""

import ctypes
import hashlib
import json
import os
import time

from napcat_control import NapCatControl, classify
from settings import ROOT, atomic_json, read_settings


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def desktop_open():
    return bool(ctypes.windll.user32.FindWindowW(None, "大肥鱼控制台"))


class StartupMonitor:
    def __init__(self):
        self.control = NapCatControl()
        self.quick_attempted = False
        self.page_opened = False
        self.waiting_since = 0

    def tick(self):
        cfg = read_settings()
        now = time.time()
        state = dict(updated=now, phase="starting", qr_available=False)
        try:
            native = self.control.call("/QQLogin/CheckLoginStatus") or {}
            account = (
                self.control.call("/QQLogin/GetQQLoginInfo") or {}
                if native.get("isLogin")
                else {}
            )
            health = read_json(ROOT / "data/health.json")
            state["phase"] = classify(native, health, account, cfg["bot_qq"], now)
            if state["phase"] == "session_unavailable" and read_json(ROOT / "data/supervisor.json").get("session_recovery_limited"):
                state["phase"] = "recovery_failed"
            state["qr_available"] = state["phase"] == "waiting_login" and bool(
                native.get("qrcodeurl")
            )
            if state["phase"] == "ready":
                self.quick_attempted = False
                self.page_opened = False
                self.waiting_since = 0
                if cfg["paused"] or not cfg["live"]:
                    state["phase"] = "paused"
            elif state["phase"] == "waiting_login":
                self.waiting_since = self.waiting_since or now
                if (
                    not self.quick_attempted
                    and native.get("loginPhase") != "qrcode_scanned"
                ):
                    self.quick_attempted = True
                    available = self.control.call("/QQLogin/GetQuickLoginList") or []
                    if cfg["bot_qq"] in [
                        str(x.get("uin") if isinstance(x, dict) else x)
                        for x in available
                    ]:
                        try:
                            self.control.call(
                                "/QQLogin/SetQuickLogin", {"uin": cfg["bot_qq"]}
                            )
                            state["phase"] = "connecting"
                            state["qr_available"] = False
                        except Exception:
                            state["quick_login_failed"] = True
                if not self.page_opened and now - self.waiting_since >= 10:
                    opened = read_json(ROOT / "data/login-page-opened.json")
                    if now - opened.get("updated", 0) >= 60 and not desktop_open():
                        os.startfile("http://127.0.0.1:8765/startup")
                        atomic_json(
                            ROOT / "data/login-page-opened.json", dict(updated=now)
                        )
                    self.page_opened = True
            state["login_phase"] = native.get("loginPhase", "")
        except Exception as exc:
            state["error"] = type(exc).__name__
        atomic_json(ROOT / "data/startup.json", state)
        return state


def main():
    if (ROOT / "config/offline-development.json").exists():
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    tag = hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:16]
    handle = kernel.CreateMutexW(None, True, "Local\\QQCompanionLogin_" + tag)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return
    try:
        monitor = StartupMonitor()
        while True:
            monitor.tick()
            time.sleep(10)
    finally:
        kernel.ReleaseMutex(ctypes.c_void_p(handle))
        kernel.CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    main()
