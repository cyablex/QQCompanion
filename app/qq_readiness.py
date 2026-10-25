"""Detect the observed half-logged-in QQ session without sending a test message."""

import asyncio


class SessionUnavailable(ConnectionError):
    pass


async def check_messaging(call, expected):
    account = await asyncio.wait_for(call("get_login_info"), 8)
    if str(account.get("user_id", "")) != expected:
        raise SessionUnavailable("wrong_account")
    friends = await asyncio.wait_for(call("get_friend_list"), 10)
    if not isinstance(friends, list):
        raise SessionUnavailable("invalid_friend_response")
    if friends:
        return len(friends)
    # An empty list is valid for a new account. But empty friends AND a failed
    # packet query reproduces the session that returned send error 1006514.
    try:
        status = await asyncio.wait_for(call("nc_get_user_status", user_id=expected), 10)
        if not isinstance(status, dict) or not isinstance(status.get("status"), int):
            raise SessionUnavailable("session_not_synchronized")
    except Exception as exc:
        raise SessionUnavailable("session_not_synchronized") from exc
    return 0


def recovery_due(phase, now, unhealthy_since, attempts):
    recent = [stamp for stamp in attempts if now - stamp < 3600]
    if phase not in {"session_unavailable", "recovery_failed"}:
        return False, 0, recent
    since = unhealthy_since or now
    return now - since >= 180 and len(recent) < 2, since, recent
