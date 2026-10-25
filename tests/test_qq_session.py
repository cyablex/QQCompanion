import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from qq_readiness import SessionUnavailable, check_messaging, recovery_due
from napcat_control import classify
from startup_page import render


@pytest.mark.asyncio
async def test_local_login_does_not_authorize_send_when_session_is_half_loaded():
    called = []

    async def call(name, **kwargs):
        called.append(name)
        if name == "get_login_info":
            return {"user_id": 12345}
        if name == "get_friend_list":
            return []
        if name == "nc_get_user_status":
            raise RuntimeError("Packet status unavailable")
        raise AssertionError("Readiness checks must not send messages")

    with pytest.raises(SessionUnavailable, match="session_not_synchronized"):
        await check_messaging(call, "12345")
    assert called == ["get_login_info", "get_friend_list", "nc_get_user_status"]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_real_friend_list_or_valid_empty_account_is_ready(empty):
    async def call(name, **kwargs):
        return {"get_login_info": {"user_id": 12345}, "get_friend_list": [] if empty else [{"user_id": 67890}], "nc_get_user_status": {"status": 10, "ext_status": 0}}[name]

    assert await check_messaging(call, "12345") == (0 if empty else 1)


@pytest.mark.asyncio
async def test_wrong_account_never_reaches_other_probes():
    async def call(name, **kwargs):
        assert name == "get_login_info"
        return {"user_id": 99999}

    with pytest.raises(SessionUnavailable, match="wrong_account"):
        await check_messaging(call, "12345")


def test_half_logged_in_session_is_not_rendered_as_ready():
    phase = classify({"isLogin": True}, {"qq_connected": True, "transport_ready": False, "updated": 999}, {"uin": "12345"}, "12345", now=1000)
    assert phase == "session_unavailable"
    page = render({"phase": phase})
    assert "暂时不能聊天" in page and "大肥鱼已就绪" not in page


def test_recovery_waits_for_persistent_failure_and_stops_after_two_attempts():
    due, since, attempts = recovery_due("session_unavailable", 1000, 0, [])
    assert not due and since == 1000
    due, since, attempts = recovery_due("session_unavailable", 1179, since, attempts)
    assert not due
    due, since, attempts = recovery_due("session_unavailable", 1180, since, attempts)
    assert due
    assert not recovery_due("recovery_failed", 1600, 1000, [1180, 1400])[0]
    assert recovery_due("session_unavailable", 5001, 1000, [1180, 1400])[0]


@pytest.mark.parametrize("phase", ["waiting_login", "wrong_account", "connecting", "ready", None])
def test_phone_verification_or_normal_connection_is_never_restarted(phase):
    due, since, _ = recovery_due(phase, 5000, 1000, [])
    assert not due and since == 0
