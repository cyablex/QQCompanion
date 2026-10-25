"""Authenticated loopback NapCat login checks; credentials stay in memory."""

import hashlib
import json
import time
import urllib.request

from settings import ROOT


class NapCatControl:
    def __init__(self):
        self.credential = None
        self.expires = 0
        # Ignore system proxies for the loopback control plane.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _post(self, path, data, credential=None):
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        request = urllib.request.Request(
            "http://127.0.0.1:6099/api" + path,
            data=json.dumps(data).encode(),
            headers=headers,
        )
        with self.http.open(request, timeout=5) as response:
            result = json.load(response)
        if result.get("code") != 0:
            raise RuntimeError("NapCat control request rejected")
        return result.get("data")

    def call(self, path, data=None):
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
            return self._post(path, data or {}, self.credential)
        except Exception:
            # Reauthenticate after a server restart; never retry a login write here.
            self.credential = None
            raise


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
