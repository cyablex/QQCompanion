import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import services
import launch
import startup_page


def test_offline_status_never_displays_login_link_or_qr(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/offline-development.json").write_text("{}")
    monkeypatch.setattr(startup_page, "ROOT", tmp_path)
    page = startup_page.render(startup_page.load_state())
    assert "离线开发" in page
    assert "/qq-login" not in page and "/qq-qr.png" not in page


def test_offline_guard_blocks_online_helpers_before_process_inspection(
    tmp_path, monkeypatch
):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/offline-development.json").write_text("{}")
    monkeypatch.setattr(services, "ROOT", tmp_path)

    def forbidden():
        raise AssertionError("Must not spawn or inspect online services")

    monkeypatch.setattr(services, "matching", forbidden)
    services.start(["qq", "bot", "login", "watchdog"])


def test_desktop_launcher_only_starts_local_settings_in_offline_mode(
    tmp_path, monkeypatch
):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/offline-development.json").write_text("{}")
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(launch, "start", lambda names: calls.append(names))
    monkeypatch.setattr(launch.sys, "argv", ["launch.py"])
    monkeypatch.setattr(
        launch.os,
        "startfile",
        lambda _: (_ for _ in ()).throw(AssertionError("No login page")),
    )
    launch.main()
    assert calls == [["setup"]]
