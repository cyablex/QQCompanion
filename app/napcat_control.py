"""Authenticated loopback NapCat login checks; credentials stay in memory."""

import hashlib
import json
import threading
import time
import urllib.request

from settings import ROOT, atomic_json

_qr_lock = threading.Lock()


class NapCatControl:
    def __init__(self):
        self.credential = None
        self.expires = 0
        # Ignore system proxies for the loopback control plane.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _post(self, path, data, credential=None, timeout=5):
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        request = urllib.request.Request(
            "http://127.0.0.1:6099/api" + path,
            data=json.dumps(data).encode(),
            headers=headers,
        )
        with self.http.open(request, timeout=timeout) as response:
            result = json.load(response)
        if result.get("code") != 0:
            raise RuntimeError("NapCat control request rejected")
        return result.get("data")

    def call(self, path, data=None, *, timeout=5):
        if not self.credential or time.time() >= self.expires:
            config = json.loads(
                (ROOT / "napcat/config/webui.json").read_text(encoding="utf-8")
            )
            digest = hashlib.sha256((config["token"] + ".napcat").encode()).hexdigest()
            login = self._post("/auth/login", {"hash": digest})
            self.credential = login.get("Credential")
            if not self.credential:
                raise RuntimeError("NapCat local authentication needs user action")
            self.expires = time.time() + 3000
        try:
            return self._post(path, data or {}, self.credential, timeout=timeout)
        except Exception:
            # Reauthenticate after a server restart; never retry a login write here.
            self.credential = None
            raise


def refresh_login_qr():
    """Request a new login code and wait for its asynchronous PNG write."""
    if (ROOT / "config/offline-development.json").exists():
        raise ValueError("当前为离线开发模式，请先恢复在线登录。")
    if not _qr_lock.acquire(blocking=False):
        raise ValueError("正在刷新二维码，请稍候。")
    try:
        control = NapCatControl()
        try:
            native = control.call("/QQLogin/CheckLoginStatus") or {}
        except Exception:
            raise ValueError("无法连接 QQ 登录服务，请检查登录管理。") from None
        if native.get("isLogin"):
            raise ValueError("QQ 已登录，无需刷新二维码。")
        if native.get("loginPhase") == "qrcode_scanned":
            raise ValueError("已扫码，请先在手机 QQ 确认或取消登录。")
        image = ROOT / "napcat/cache/qrcode.png"
        previous = image.read_bytes() if image.is_file() else None
        try:
            refreshed = control.call("/QQLogin/RefreshQRcode", timeout=15) or {}
        except Exception:
            raise ValueError("二维码刷新未完成，请稍后重试或打开登录管理。") from None
        if refreshed.get("restarting") or not refreshed.get("qrcodeurl"):
            raise ValueError("QQ 登录服务正在重连，请稍后再刷新二维码。")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = image.read_bytes() if image.is_file() else b""
            if current != previous and current.startswith(b"\x89PNG\r\n\x1a\n") and current.endswith(b"IEND\xaeB`\x82"):
                latest = control.call("/QQLogin/CheckLoginStatus") or {}
                if not latest.get("isLogin"):
                    atomic_json(ROOT / "data/startup.json", dict(
                        updated=time.time(), phase=classify(latest, {}, {}, ""),
                        qr_available=bool(latest.get("qrcodeurl")),
                        login_phase=latest.get("loginPhase", ""),
                    ))
                return current
            time.sleep(0.1)
        raise ValueError("新的二维码图片尚未准备好，请稍后重试。")
    finally:
        _qr_lock.release()


def classify(native, health, account, expected, now=None):
    now = time.time() if now is None else now
    if native.get("isLogin"):
        if str(account.get("uin", account.get("user_id", ""))) != expected:
            return "wrong_account"
        if health.get("transport_ready") is False and 0 <= now - health.get("updated", 0) < 90:
            return "session_unavailable"
        if health.get("qq_connected") and 0 <= now - health.get("updated", 0) < 90:
            return "ready"
        return "connecting"
    phase = native.get("loginPhase")
    if phase in {"waiting_qrcode", "qrcode_scanned"} or (
        not phase and native.get("qrcodeurl")
    ):
        return "waiting_login"
    return "reconnecting"
