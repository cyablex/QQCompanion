import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import social
from social import Social
from store import Store


@pytest.mark.asyncio
async def test_friend_accept_only_enabled_test_contact(tmp_path, monkeypatch):
    store = Store(tmp_path / "test.sqlite3")
    cfg = dict(
        live=True,
        paused=False,
        friend_accept=True,
        owner_qq="12345",
        bot_qq="67890",
        revision=1,
        guest_mode="allowlist",
        guest_ids=["11111"],
    )
    monkeypatch.setattr(social, "read_settings", lambda: cfg)
    calls = []

    async def check():
        pass

    async def action(name, **kwargs):
        calls.append(name)
        return [{"user_id": 11111}] if name == "get_friend_list" else None

    plugin = SimpleNamespace(
        store=store, bot=SimpleNamespace(call_action=action), check_account=check
    )
    st = Social(plugin)
    store.db.execute(
        "INSERT INTO friends VALUES(?,?,?,?,?,?)",
        ("allowed", "11111", "flag1", "", "pending", time.time()),
    )
    await st.friends()
    assert calls == ["set_friend_add_request", "get_friend_list"]
    assert (
        store.db.execute("SELECT state FROM friends WHERE id='allowed'").fetchone()[0]
        == "accepted"
    )
    await st.friends()
    assert len(calls) == 2
    store.db.execute(
        "INSERT INTO friends VALUES(?,?,?,?,?,?)",
        ("other", "22222", "flag2", "", "pending", time.time()),
    )
    await st.friends()
    assert len(calls) == 2
    store.db.close()


@pytest.mark.asyncio
async def test_delayed_friend_confirmation_recovers_without_second_approval(
    tmp_path, monkeypatch
):
    import hashlib

    store = Store(tmp_path / "friend.sqlite3")
    monkeypatch.setattr(social, "read_settings", lambda: {"guest_ids": ["11111"]})
    store.db.execute(
        "INSERT INTO friends VALUES(?,?,?,?,?,?)",
        ("request", "11111", "flag", "", "unknown", time.time()),
    )
    key = "friend:" + hashlib.sha256(b"request").hexdigest()
    store.action(key, "11111", "friend_accept", [])
    store.outcome(key, "unknown")
    calls = []

    async def check():
        pass

    async def action(name, **kwargs):
        calls.append(name)
        return [{"user_id": 11111}]

    st = Social(
        SimpleNamespace(
            store=store, check_account=check, bot=SimpleNamespace(call_action=action)
        )
    )
    await st.reconcile_friends()
    assert calls == ["get_friend_list"]
    assert store.get("friend_probe")["test_connected"] == 1
    assert store.get("known_friend_ids") == ["11111"]
    assert (
        store.db.execute("SELECT state FROM actions WHERE id=?", (key,)).fetchone()[0]
        == "succeeded"
    )
    assert store.db.execute("SELECT state FROM friends").fetchone()[0] == "accepted"
    store.db.close()


@pytest.mark.asyncio
async def test_uncertain_friend_accept_never_retries(tmp_path, monkeypatch):
    store = Store(tmp_path / "test.sqlite3")
    cfg = dict(
        live=True,
        paused=False,
        friend_accept=True,
        owner_qq="12345",
        bot_qq="67890",
        revision=1,
        guest_mode="open",
    )
    monkeypatch.setattr(social, "read_settings", lambda: cfg)
    calls = []

    async def check():
        pass

    async def action(name, **kwargs):
        calls.append(name)
        raise TimeoutError()

    st = Social(
        SimpleNamespace(
            store=store, bot=SimpleNamespace(call_action=action), check_account=check
        )
    )
    store.db.execute(
        "INSERT INTO friends VALUES(?,?,?,?,?,?)",
        ("id", "11111", "flag", "", "pending", time.time()),
    )
    with pytest.raises(TimeoutError):
        await st.friends()
    await st.friends()
    assert calls == ["set_friend_add_request"]
    assert (
        store.db.execute("SELECT state FROM friends WHERE id='id'").fetchone()[0]
        == "unknown"
    )
    store.db.close()


@pytest.mark.asyncio
async def test_qzone_disabled_never_touches_transport(tmp_path, monkeypatch):
    store = Store(tmp_path / "test.sqlite3")
    monkeypatch.setattr(
        social, "read_settings", lambda: dict(live=True, paused=False, qzone=False)
    )

    async def check():
        raise AssertionError("Transport must remain untouched")

    st = Social(SimpleNamespace(store=store, check_account=check))
    await st.qzone()
    store.db.close()


@pytest.mark.asyncio
async def test_qzone_baseline_and_verified_comment_are_deduplicated(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "qzone.sqlite3")
    cfg = dict(
        live=True,
        paused=False,
        qzone=True,
        owner_qq="12345",
        bot_qq="67890",
        revision=1,
    )
    monkeypatch.setattr(social, "read_settings", lambda: cfg)
    feed = [
        {
            "uin": 12345,
            "tid": "old",
            "text": "test",
            "create_time": time.time(),
            "comments": [],
        }
    ]

    class Parser:
        @staticmethod
        def parse_feeds(items):
            return [SimpleNamespace(**x) for x in items]

    monkeypatch.setitem(
        sys.modules, "qzone_vendor.core.qzone", SimpleNamespace(QzoneParser=Parser)
    )
    writes = []

    class API:
        async def get_feeds(self, *a, **kw):
            return SimpleNamespace(ok=True, data={"msglist": feed})

        async def comment(self, post, content):
            writes.append(content)
            item = next(x for x in feed if x["tid"] == post.tid)
            item["comments"] = [SimpleNamespace(uin=67890, content=content)]
            return SimpleNamespace(ok=True)

        async def get_detail(self, post):
            return SimpleNamespace(
                ok=True, data=next(x for x in feed if x["tid"] == post.tid)
            )

    async def check():
        pass

    async def decide(contact, kind, instruction, public=False):
        assert public and contact == "12345"
        return {"action": "reply", "messages": ["a public comment"], "revision": 1}

    engine = SimpleNamespace(decision=decide, send_lock=asyncio.Lock())
    st = Social(SimpleNamespace(store=store, check_account=check, engine=engine))
    st.qzone_api = API()
    await st.qzone(read_only=True, establish_baseline=True)
    assert not writes
    await st.qzone()
    assert not writes
    feed.insert(
        0,
        {
            "uin": 12345,
            "tid": "new",
            "text": "new post",
            "create_time": time.time(),
            "comments": [],
        },
    )
    await st.qzone(force_latest=True)
    assert len(writes) == 1
    await st.qzone(force_latest=True)
    assert len(writes) == 1
    row = store.db.execute(
        "SELECT state FROM actions WHERE kind='qzone_comment'"
    ).fetchone()
    assert row[0] == "succeeded"
    store.db.close()
