# ruff: noqa: E402
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from napcat_control import classify
import startup_monitor
import startup_page


@pytest.mark.parametrize(
    "native,health,account,phase",
    [
        (
            {
                "isLogin": False,
                "loginPhase": "waiting_qrcode",
                "qrcodeurl": "private-qr-url",
            },
            {"qq_connected": True, "updated": 999},
            {},
            "waiting_login",
        ),
        (
            {"isLogin": True},
            {"qq_connected": True, "updated": 999},
            {"uin": "12345"},
            "ready",
        ),
        (
            {"isLogin": True},
            {"qq_connected": True, "updated": 800},
            {"uin": "12345"},
            "connecting",
        ),
        (
            {"isLogin": True},
            {"qq_connected": False, "updated": 999},
            {"uin": "12345"},
            "connecting",
        ),
        (
            {"isLogin": True},
            {"qq_connected": True, "updated": 999},
            {"uin": "99999"},
            "wrong_account",
        ),
        ({"isLogin": False, "loginPhase": "reconnecting"}, {}, {}, "reconnecting"),
        (
            {"isLogin": False, "loginPhase": "reconnecting", "qrcodeurl": "stale-qr"},
            {},
            {},
            "reconnecting",
        ),
    ],
)
def test_process_existence_or_stale_health_never_means_ready(
    native, health, account, phase
):
    assert classify(native, health, account, "12345", now=1000) == phase


@pytest.mark.parametrize("native_window", [False, True])
def test_qr_opened_once_and_never_for_wrong_account(tmp_path, monkeypatch, native_window):
    monkeypatch.setattr(startup_monitor, "desktop_open", lambda: native_window)
    monkeypatch.setattr(startup_monitor, "ROOT", tmp_path)
    monkeypatch.setattr(
        startup_monitor,
        "read_settings",
        lambda: dict(bot_qq="12345", live=True, paused=False),
    )
    moment = [1000]
    monkeypatch.setattr(startup_monitor.time, "time", lambda: moment[0])
    opened = []
    monkeypatch.setattr(startup_monitor.os, "startfile", opened.append)
    monitor = startup_monitor.StartupMonitor()

    class Control:
        def call(self, path, *args):
            if path.endswith("CheckLoginStatus"):
                return dict(
                    isLogin=False, loginPhase="waiting_qrcode", qrcodeurl="SECRET QR"
                )
            if path.endswith("GetQuickLoginList"):
                return []
            raise AssertionError("Only expected account can be logged in")

    monitor.control = Control()
    monitor.tick()
    moment[0] += 15
    monitor.tick()
    moment[0] += 15
    state = monitor.tick()
    assert opened == ([] if native_window else ["http://127.0.0.1:8765/startup"])
    assert "SECRET" not in json.dumps(state)
    assert "SECRET" not in (tmp_path / "data/startup.json").read_text()


def test_old_ready_file_is_not_displayed_as_current_ready(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    (tmp_path / "data/startup.json").write_text(
        json.dumps(dict(phase="ready", updated=0))
    )
    monkeypatch.setattr(startup_page, "ROOT", tmp_path)
    assert startup_page.load_state()["phase"] == "status_unavailable"
    page = startup_page.render(dict(phase="waiting_login", qr_available=True))
    assert "/qq-qr.png" in page and "大肥鱼已就绪" not in page


def test_login_page_serves_fresh_qr_and_hides_it_after_login(tmp_path, monkeypatch):
    import threading
    import urllib.request
    import urllib.error
    from http.server import ThreadingHTTPServer
    import setup_server

    image = tmp_path / "napcat/cache/qrcode.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(setup_server, "ROOT", tmp_path)
    state = dict(phase="waiting_login", qr_available=True)
    monkeypatch.setattr(startup_page, "load_state", lambda: state)
    server = ThreadingHTTPServer(("127.0.0.1", 0), setup_server.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def fetch(path):
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}" + path,
            headers={"Host": "127.0.0.1:8765"},
        )
        return urllib.request.urlopen(request, timeout=3)

    try:
        with fetch("/startup") as response:
            assert "需要你确认 QQ 登录" in response.read().decode()
            assert "img-src 'self'" in response.headers["Content-Security-Policy"]
        with fetch("/qq-qr.png") as response:
            assert response.read() == image.read_bytes()
            assert response.headers["Cache-Control"] == "no-store"
        state.update(phase="ready")
        with pytest.raises(urllib.error.HTTPError) as failure:
            fetch("/qq-qr.png")
        assert failure.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)
