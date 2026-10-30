"""Login QR refresh uses a real regeneration request, not a status poll."""
import base64
import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import napcat_control
import desktop_api
import setup_server
from http.server import ThreadingHTTPServer

PNG = b"\x89PNG\r\n\x1a\nnew-code-IEND\xaeB`\x82"


@pytest.fixture
def login(tmp_path, monkeypatch):
    monkeypatch.setattr(napcat_control, "ROOT", tmp_path)
    image = tmp_path / "napcat/cache/qrcode.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"old-code")
    native = dict(isLogin=False, loginPhase="waiting_qrcode", qrcodeurl="old-private-url")
    calls = []

    def call(self, path, data=None, **kwargs):
        calls.append((path, kwargs))
        if path.endswith("CheckLoginStatus"):
            return native
        assert path.endswith("RefreshQRcode")
        native["qrcodeurl"] = "new-private-url"
        return dict(qrcodeurl=native["qrcodeurl"])

    monkeypatch.setattr(napcat_control.NapCatControl, "call", call)
    # Simulate NapCat's delayed asynchronous PNG write.
    monkeypatch.setattr(napcat_control.time, "sleep", lambda seconds: image.write_bytes(PNG))
    return tmp_path, image, native, calls


def test_refresh_waits_for_new_image_and_replaces_stale_monitor_state(login):
    root, image, native, calls = login
    assert napcat_control.refresh_login_qr() == PNG
    assert ("/QQLogin/RefreshQRcode", {"timeout": 15}) in calls
    state = json.loads((root / "data/startup.json").read_text())
    assert state["phase"] == "waiting_login" and state["qr_available"]
    assert "private-url" not in json.dumps(state)


@pytest.mark.parametrize("state", [dict(isLogin=True), dict(loginPhase="qrcode_scanned")])
def test_logged_in_or_scanned_session_is_not_replaced(login, state):
    root, image, native, calls = login
    native.update(state)
    with pytest.raises(ValueError):
        napcat_control.refresh_login_qr()
    assert all(not path.endswith("RefreshQRcode") for path, _ in calls)
    assert image.read_bytes() == b"old-code"


def test_offline_and_duplicate_requests_do_not_call_login_service(login):
    root, _, _, calls = login
    (root / "config").mkdir()
    marker = root / "config/offline-development.json"
    marker.write_text("{}")
    with pytest.raises(ValueError, match="离线"):
        napcat_control.refresh_login_qr()
    marker.unlink()
    napcat_control._qr_lock.acquire()
    try:
        with pytest.raises(ValueError, match="正在刷新"):
            napcat_control.refresh_login_qr()
    finally:
        napcat_control._qr_lock.release()
    assert calls == []


@pytest.mark.parametrize("failure", ["reconnecting", "timeout", "image"])
def test_failure_does_not_return_expired_code_or_retry_write(login, monkeypatch, failure):
    _, image, native, calls = login
    original = napcat_control.NapCatControl.call

    def call(self, path, data=None, **kwargs):
        if path.endswith("RefreshQRcode") and failure != "image":
            calls.append((path, kwargs))
            if failure == "timeout":
                raise TimeoutError("private-token")
            return dict(restarting=True)
        return original(self, path, data, **kwargs)

    monkeypatch.setattr(napcat_control.NapCatControl, "call", call)
    moment = [0.0]
    monkeypatch.setattr(napcat_control.time, "monotonic", lambda: moment[0])
    def sleep(seconds):
        moment[0] += 1
        image.write_bytes(b"\x89PNG\r\n\x1a\npartial")
    monkeypatch.setattr(napcat_control.time, "sleep", sleep)
    with pytest.raises(ValueError) as exc:
        napcat_control.refresh_login_qr()
    assert "private-token" not in str(exc.value)
    assert sum(path.endswith("RefreshQRcode") for path, _ in calls) == 1
    assert not napcat_control._qr_lock.locked()


def test_native_bridge_returns_new_image_and_readable_failure(monkeypatch):
    monkeypatch.setattr(napcat_control, "refresh_login_qr", lambda: PNG)
    result = desktop_api.DesktopAPI().refresh_login_qr()
    assert result == dict(ok=True, data="data:image/png;base64," + base64.b64encode(PNG).decode())
    def fail():
        raise ValueError("QQ 已登录，无需刷新二维码。")
    monkeypatch.setattr(napcat_control, "refresh_login_qr", fail)
    assert desktop_api.DesktopAPI().refresh_login_qr()["message"] == "QQ 已登录，无需刷新二维码。"


def test_browser_refresh_requires_csrf_and_redirects_after_regeneration(monkeypatch):
    calls = []
    monkeypatch.setattr(napcat_control, "refresh_login_qr", lambda: calls.append(True))
    server = ThreadingHTTPServer(("127.0.0.1", 0), setup_server.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    def post(token):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request("POST", "/qq-qr/refresh", "csrf=" + token,
                           {"Host": "127.0.0.1:8765", "Content-Type": "application/x-www-form-urlencoded"})
        response = connection.getresponse()
        result = (response.status, response.getheader("Location"))
        response.read()
        connection.close()
        return result
    try:
        assert post("wrong") == (403, None)
        assert calls == []
        assert post(setup_server.CSRF) == (303, "/startup")
        assert calls == [True]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
