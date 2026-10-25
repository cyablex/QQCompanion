import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from connection_alerts import ConnectionAlerts, connection_phase


def test_short_drop_is_silent_and_sustained_outage_recovers_once(tmp_path):
    notices = []
    alerts = ConnectionAlerts(tmp_path / "alerts.json", lambda *x: notices.append(x))
    alerts.tick("ready", 1000)
    alerts.tick("connecting", 1010)
    alerts.tick("ready", 1050)
    assert notices == []
    alerts.tick("session_unavailable", 1100)
    alerts.tick("session_unavailable", 1159)
    assert notices == []
    alerts.tick("session_unavailable", 1160)
    alerts.tick("waiting_login", 1200)
    assert len(notices) == 1
    assert "异常" in notices[0][0]
    alerts.tick("ready", 1250)
    alerts.tick("ready", 1265)
    assert len(notices) == 2
    assert "恢复" in notices[1][0]


def test_first_launch_has_grace_and_monitor_restart_does_not_repeat(tmp_path):
    notices = []
    path = tmp_path / "alerts.json"
    def notify(*args):
        notices.append(args)

    alerts = ConnectionAlerts(path, notify)
    alerts.tick("waiting_login", 1000)
    alerts.tick("waiting_login", 1089)
    assert not notices
    alerts.tick("waiting_login", 1090)
    alerts = ConnectionAlerts(path, notify)
    alerts.tick("waiting_login", 2000)
    assert len(notices) == 1
    alerts.tick("ready", 2015)
    assert len(notices) == 2


@pytest.mark.parametrize("disabled", ["paused", "live_off"])
def test_deliberate_pause_does_not_alert_or_send_false_recovery(tmp_path, disabled):
    notices = []
    alerts = ConnectionAlerts(tmp_path / "alerts.json", lambda *x: notices.append(x))
    alerts.tick("ready", 1000)
    alerts.tick("connecting", 1010)
    alerts.tick("paused" if disabled == "paused" else "bot_stopped", 1100, enabled=disabled != "live_off")
    alerts.tick("ready", 1200)
    assert notices == []


@pytest.mark.parametrize("initial", [True, False])
def test_recovery_exhaustion_needs_one_action_reminder(tmp_path, initial):
    notices = []
    alerts = ConnectionAlerts(tmp_path / "alerts.json", lambda *x: notices.append(x))
    alerts.tick("ready", 1000)
    alerts.tick("recovery_failed" if initial else "session_unavailable", 1010)
    alerts.tick("recovery_failed" if initial else "session_unavailable", 1070)
    alerts.tick("recovery_failed", 1400)
    alerts.tick("recovery_failed", 1415)
    assert len(notices) == (1 if initial else 2)


def test_failure_to_launch_notice_can_retry_without_losing_outage(tmp_path):
    def fail(*args):
        raise OSError("desktop unavailable")

    alerts = ConnectionAlerts(tmp_path / "alerts.json", fail)
    alerts.tick("connecting", 1000)
    with pytest.raises(OSError):
        alerts.tick("connecting", 1090)
    assert not alerts.state.get("notified")


def test_stale_files_and_missing_bot_cannot_claim_recovery():
    startup = {"phase": "ready", "updated": 1000}
    health = {"transport_ready": True, "updated": 1000}
    assert connection_phase(startup, health, True, 1001) == "ready"
    assert connection_phase(startup, health, False, 1001) == "bot_stopped"
    assert connection_phase(startup, health, True, 1045) == "status_unavailable"
    startup["updated"] = 1100
    assert connection_phase(startup, health, True, 1100) == "status_unavailable"
    health.update(updated=1100, transport_ready=False)
    assert connection_phase(startup, health, True, 1100) == "status_unavailable"
