import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import desktop_api
import game_mode
import relay_setup
import settings


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    (tmp_path / "data").mkdir()
    for module in (desktop_api, settings, game_mode, relay_setup):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    cfg = settings.read_settings()
    cfg.update(
        owner_qq="123456",
        bot_qq="654321",
        name="大肥鱼",
        live=True,
        model_route="relay",
        relay_connection={
            "base_url": "https://active.example/v1",
            "model": "active-model",
        },
    )
    settings.atomic_json(tmp_path / "config/user.json", cfg)
    monkeypatch.setattr(
        desktop_api.services, "matching", lambda: {"bot": object(), "qq": object()}
    )
    return desktop_api.DesktopAPI(), tmp_path


def form(api):
    return api.get_settings()["data"]["values"]


def test_custom_window_buttons_and_resize_do_not_touch_backend(desktop, monkeypatch):
    api, _ = desktop
    window = SimpleNamespace(native=SimpleNamespace(WindowState="Normal"))
    calls = []

    def state(value):
        window.native.WindowState = value
        calls.append(value)

    window.maximize = lambda: state("Maximized")
    window.minimize = lambda: state("Minimized")
    window.restore = lambda: state("Normal")
    window.destroy = lambda: calls.append("closed")
    window.resize = lambda w, h: calls.append((w, h))
    monkeypatch.setattr(
        desktop_api.services,
        "start",
        lambda *args: pytest.fail("Window control must not start the bot"),
    )
    assert not api.window_action("maximize")["ok"]
    api._attach_window(window)
    assert not api.window_action("unsupported")["ok"]
    assert api.window_action("maximize")["data"]["maximized"]
    assert api.resize_window(1000, 700)["ok"] and calls == ["Maximized"]
    assert not api.window_action("maximize")["data"]["maximized"]
    assert api.resize_window(100, 100)["ok"] and calls[-1] == (960, 680)
    assert api.resize_window(1000, 720)["ok"] and calls[-1] == (1000, 720)
    assert api.window_action("minimize")["ok"] and calls[-1] == "Minimized"
    assert api.window_action("close")["ok"] and calls[-1] == "closed"


def test_status_requires_live_processes_and_fresh_healthy_connection(
    desktop, monkeypatch
):
    api, root = desktop
    settings.atomic_json(
        root / "data/health.json",
        dict(updated=time.time(), qq_connected=True, transport_ready=True),
    )
    assert api.get_status()["data"]["phase"] == "ready"
    monkeypatch.setattr(desktop_api.services, "matching", lambda: {})
    result = api.get_status()["data"]
    assert result["phase"] == "stopped" and not result["connected"]
    monkeypatch.setattr(desktop_api.services, "matching", lambda: {"bot": object()})
    settings.atomic_json(
        root / "data/health.json",
        dict(updated=time.time() - 200, qq_connected=True, transport_ready=True),
    )
    assert not api.get_status()["data"]["connected"]


@pytest.mark.parametrize("voice_state,ready", [("warming", False), ("ready", True), ("unavailable", True)])
def test_startup_waits_for_voice_and_allows_explicit_text_degradation(desktop, monkeypatch, voice_state, ready):
    api, root = desktop
    cfg = settings.read_settings()
    cfg.update(voice_replies=True, game_mode=False)
    settings.atomic_json(root / "config/user.json", cfg)
    settings.atomic_json(root / "data/health.json", dict(updated=time.time(), qq_connected=True, transport_ready=True))
    monkeypatch.setattr(desktop_api, "read_voice_status", lambda *args: dict(state=voice_state, ready=voice_state == "ready", message="status"))
    status = api.get_status()["data"]
    assert status["connected"] and status["startup_ready"] is ready


def test_game_mode_never_holds_startup_for_voice(desktop):
    api, root = desktop
    cfg = settings.read_settings()
    cfg.update(voice_replies=True, game_mode=True)
    settings.atomic_json(root / "config/user.json", cfg)
    settings.atomic_json(root / "data/health.json", dict(updated=time.time(), qq_connected=True, transport_ready=True))
    status = api.get_status()["data"]
    assert status["startup_ready"] and status["voice"]["state"] == "game_mode"


def test_start_waits_for_a_new_heartbeat_without_changing_pause(desktop, monkeypatch):
    api, root = desktop
    cfg = settings.read_settings()
    cfg["paused"] = True
    settings.atomic_json(root / "config/user.json", cfg)
    settings.atomic_json(root / "data/health.json", dict(updated=time.time() - 1, qq_connected=True, transport_ready=True))
    processes = {}
    monkeypatch.setattr(desktop_api.services, "matching", lambda: processes)
    starts = []

    def start(names):
        starts.append(names)
        processes.update(bot=object(), qq=object())

    monkeypatch.setattr(desktop_api.services, "start", start)
    assert api.control("start")["ok"]
    assert not api.get_status()["data"]["connected"]
    settings.atomic_json(root / "data/health.json", dict(updated=time.time(), qq_connected=True, transport_ready=True))
    assert api.get_status()["data"]["connected"]
    assert api.get_status()["data"]["phase"] == "paused"
    assert settings.read_settings()["paused"]
    assert len(starts) == 1


def test_existing_ready_backend_is_reused_and_login_state_blocks_old_health(desktop, monkeypatch):
    api, root = desktop
    settings.atomic_json(root / "data/health.json", dict(updated=time.time(), qq_connected=True, transport_ready=True))
    monkeypatch.setattr(desktop_api.services, "start", lambda names: None)
    assert api.control("start")["ok"]
    assert api.get_status()["data"]["connected"]
    for phase in ("wrong_account", "waiting_login", "session_unavailable"):
        settings.atomic_json(root / "data/startup.json", dict(updated=time.time(), phase=phase))
        state = api.get_status()["data"]
        assert not state["connected"] and state["phase"] == phase


def test_login_help_opens_only_the_existing_local_page(desktop, monkeypatch):
    api, _ = desktop
    opened, started = [], []
    monkeypatch.setattr(desktop_api.os, "startfile", opened.append)
    monkeypatch.setattr(desktop_api.services, "start", started.append)
    assert api.open_login_page()["data"]["opened"]
    assert opened == ["http://127.0.0.1:8765/startup"] and started == [["setup"]]


def test_settings_are_validated_before_writing_credentials_and_preserve_other_fields(
    desktop, monkeypatch
):
    api, root = desktop
    writes = []
    monkeypatch.setattr(desktop_api, "save_key", lambda value: writes.append(value))
    original = (root / "config/user.json").read_bytes()
    values = form(api)
    values.update(
        name="新名字", api_key="test-secret-long-enough", guest_ids=["invalid"]
    )
    assert not api.save_settings(values)["ok"]
    assert not writes and (root / "config/user.json").read_bytes() == original
    values.update(guest_ids=[], daily_cny=3)
    assert api.save_settings(values)["ok"]
    cfg = settings.read_settings()
    assert cfg["name"] == "新名字" and cfg["revision"] == 2
    assert cfg["relay_connection"]["model"] == "active-model" and cfg["live"]
    assert writes == ["test-secret-long-enough"]
    assert not api.save_settings(values)["ok"]  # Reject stale form snapshots.


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 0])
def test_invalid_budget_is_not_saved(desktop, bad):
    api, root = desktop
    original = (root / "config/user.json").read_bytes()
    values = form(api)
    values["daily_cny"] = bad
    assert not api.save_settings(values)["ok"]
    assert (root / "config/user.json").read_bytes() == original


def test_identity_change_disables_online_actions_even_in_open_mode(desktop):
    api, _ = desktop
    values = form(api)
    values.update(bot_qq="999999", guest_mode="open", proactive=True, qzone=True)
    result = api.save_settings(values)
    assert result["data"]["restart_required"] and result["data"]["identity_changed"]
    cfg = settings.read_settings()
    assert not any(
        cfg[x] for x in ("live", "proactive", "guests", "friend_accept", "qzone")
    )


def test_editing_persona_does_not_reenable_separately_disabled_friend_features(desktop):
    api, root = desktop
    cfg = settings.read_settings()
    cfg.update(guest_mode="open", guests=False, friend_accept=False)
    settings.atomic_json(root / "config/user.json", cfg)
    values = form(api)
    values["persona"] = "新的说话习惯"
    assert api.save_settings(values)["ok"]
    assert not settings.read_settings()["guests"]
    assert not settings.read_settings()["friend_accept"]


def test_saved_settings_never_return_keys_or_activate_unverified_relay(
    desktop, monkeypatch
):
    api, root = desktop
    writes = []
    monkeypatch.setattr(
        relay_setup, "save_key", lambda key, **kwargs: writes.append(key)
    )
    (root / "config/relay-api-key.dpapi").touch()
    original = (root / "config/user.json").read_bytes()
    assert api.save_relay(
        dict(
            base_url="https://new.example/v1",
            model="new-model",
            relay_key="test-relay-key-long",
        )
    )["ok"]
    assert (root / "config/user.json").read_bytes() == original
    result = api.get_settings()
    assert "test-relay-key-long" not in json.dumps(result)
    assert result["data"]["active_relay"]["model"] == "active-model"
    assert writes == ["test-relay-key-long"]


def test_control_stops_supervisor_first_and_keeps_settings_service(
    desktop, monkeypatch
):
    api, _ = desktop
    calls = []
    monkeypatch.setattr(
        desktop_api.subprocess, "run", lambda args, **kwargs: calls.append(args)
    )
    monkeypatch.setattr(
        desktop_api.game_mode, "close_local_voice", lambda: calls.append("voice")
    )
    assert api.control("stop")["ok"]
    assert calls[0][3:] == ["watchdog", "login", "bot", "voice", "qq"]
    assert "setup" not in calls[0] and calls[1] == "voice"


def test_start_does_not_clear_pause_and_restart_only_touches_bot_and_watchdog(
    desktop, monkeypatch
):
    api, root = desktop
    cfg = settings.read_settings()
    cfg.update(paused=True)
    settings.atomic_json(root / "config/user.json", cfg)
    starts, stops = [], []
    monkeypatch.setattr(
        desktop_api.services, "start", lambda names: starts.append(names)
    )
    monkeypatch.setattr(
        desktop_api.subprocess, "run", lambda args, **kwargs: stops.append(args)
    )
    assert api.control("start")["ok"] and settings.read_settings()["paused"]
    assert api.control("restart_bot")["ok"]
    assert starts[-1] == ["bot", "watchdog"] and stops[-1][3:] == ["watchdog", "bot"]
    (root / "config/offline-development.json").touch()
    assert not api.control("start")["ok"] and not api.control("resume")["ok"]


def test_game_switch_and_pause_use_existing_controls(desktop, monkeypatch):
    api, _ = desktop
    calls = []
    monkeypatch.setattr(
        desktop_api.game_mode,
        "set_game_mode",
        lambda enabled: calls.append(enabled) or dict(denied=[]),
    )
    assert not api.set_game_mode("off")["ok"]
    assert api.set_game_mode(True)["ok"] and calls == [True]
    assert api.control("pause")["ok"] and settings.read_settings()["paused"]
    assert api.control("resume")["ok"] and not settings.read_settings()["paused"]


def test_budget_reads_current_day_from_database_without_recovery_writes(desktop):
    api, root = desktop
    moment = desktop_api.datetime.now(desktop_api.TZ)
    path = root / "data/companion.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE calls(day TEXT,month TEXT,kind TEXT,amount REAL,state TEXT)")
        db.execute("CREATE TABLE actions(state TEXT)")
        db.execute("INSERT INTO actions VALUES('sending')")
        for kind, amount in [
            ("reply", 0.2),
            ("web_search", 0.1),
            ("validation_probe", 0.4),
        ]:
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?)",
                (moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m"), kind, amount, "reserved" if kind == "web_search" else "completed"),
            )
    budget = api.get_status()["data"]["budget"]
    assert {k: budget[k] for k in ("today", "validation", "month")} == dict(today=0.3, validation=0.4, month=0.7)
    assert {row["id"]: row["amount"] for row in budget["breakdown"]} == dict(chat=0.2, web=0.1)
    assert budget["unconfirmed"] == 0 and budget["active_reserve"] == .1 and budget["validation_calls"] == 1
    assert budget["usage_estimate"] == .2 and budget["month_usage_estimate"] == .6
    assert budget["month_unconfirmed"] == 0 and budget["month_active_reserve"] == .1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT state FROM actions").fetchone()[0] == "sending"
        assert db.execute("SELECT state FROM calls WHERE kind='web_search'").fetchone()[0] == "reserved"


def test_qr_is_returned_only_for_fresh_waiting_login_state(desktop):
    api, root = desktop
    path = root / "napcat/cache/qrcode.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"test-image")
    for age, phase in [(100, "waiting_login"), (0, "ready")]:
        settings.atomic_json(
            root / "data/startup.json",
            dict(updated=time.time() - age, phase=phase, qr_available=True),
        )
        assert api.get_qr()["data"] is None
    settings.atomic_json(
        root / "data/startup.json",
        dict(updated=time.time(), phase="waiting_login", qr_available=True),
    )
    assert api.get_qr()["data"].startswith("data:image/png;base64,")


def test_budget_reads_new_billing_snapshots_without_attributing_unknown_or_validation(desktop):
    from store import Store

    api, root = desktop
    cfg = dict(daily_calls=100, daily_cny=3, monthly_cny=60)
    store = Store(root / "data/companion.sqlite3")
    usage = dict(billing_context=dict(route="official", period="offpeak"))
    rid = store.reserve(cfg, "image_understanding_official_fallback", .1)
    store.settle(rid, .02, usage=usage)
    rid = store.reserve(cfg, "validation_probe", .05)
    store.settle(rid, .01, usage=usage)
    store.reserve(cfg, "reply", .1)
    store.db.close()
    budget = api.get_status()["data"]["budget"]
    assert budget["usage_estimate"] == .02 and budget["active_reserve"] == .1 and budget["unconfirmed"] == 0
    assert budget["periods"] == [dict(id="official_offpeak", label="官方 · 空闲", amount=.02)]
