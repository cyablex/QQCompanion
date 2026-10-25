# ruff: noqa: E402
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import offline_recovery
from engine import Engine
from media import MediaPipeline
from offline_recovery import OfflineRecovery, source_key
from reply_queue import ReplyQueue
from store import Store


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = dict(
        owner_qq="12345",
        bot_qq="67890",
        live=True,
        paused=False,
        guests=True,
        guest_mode="open",
        revision=1,
        offline_recovery=True,
        name="fish",
        persona="kind",
        owner_title="主人",
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
    )
    store = Store(tmp_path / "offline.sqlite3")
    store.set("known_friend_ids", ["11111"])
    histories, calls, queued, sent = {}, [], [], []

    async def check():
        pass

    async def action(name, **args):
        calls.append((name, args))
        if name == "get_recent_contact":
            return [
                dict(peerUin=c, chatType=1, lastestMsg=h[-1] if h else None)
                for c, h in histories.items()
            ] + [dict(peerUin="22222", chatType=2)]
        return dict(messages=histories.get(args["user_id"], []))

    async def generate(*args, **kwargs):
        return "刚看到，收到啦", None

    async def send(contact, text, **options):
        sent.append((contact, text))
        return dict(message_id=len(sent))

    async def drain(contact):
        queued.append(contact)

    plugin = SimpleNamespace(
        store=store,
        check_account=check,
        bot=SimpleNamespace(call_action=action),
        social=SimpleNamespace(reconcile_friends=check),
        workers={},
        pending={},
        drain=drain,
        reply_queue=ReplyQueue(store),
        engine=Engine(store, generate, send, lambda: cfg),
    )
    plugin.media = MediaPipeline(plugin)
    monkeypatch.setattr(offline_recovery, "read_settings", lambda: cfg)
    yield cfg, store, plugin, histories, calls, queued, sent
    store.db.close()


def msg(sender, number, text="hello", age=120, kind="text"):
    data = dict(text=text) if kind == "text" else dict(file="opaque-qq-id")
    return dict(
        message_id=number,
        real_seq=str(number),
        time=int(time.time() - age),
        sender=dict(user_id=int(sender)),
        message=[dict(type=kind, data=data)],
    )


@pytest.mark.asyncio
async def test_merge_only_unanswered_tail_and_never_execute_old_commands(env):
    cfg, store, plugin, histories, calls, queued, sent = env
    histories["12345"] = [
        msg("12345", 1, "old", 300),
        msg("67890", 2, "answered", 280),
        msg("12345", 3, "one", 180),
        msg("12345", 4, "two", 160),
        msg("12345", 5, "/暂停", 140),
    ]
    histories["99999"] = [msg("99999", 6, "stranger")]
    recovery = OfflineRecovery(plugin)
    report = await recovery.run()
    await asyncio.gather(*plugin.workers.values())
    assert report["imported"] == 2 and queued == ["12345"]
    assert [x["content"] for x in store.history("12345")] == ["one", "two"]
    assert cfg["paused"] is False
    assert all(args.get("user_id") not in {"99999", "22222"} for name, args in calls)
    plugin.workers.clear()
    await recovery.run()
    assert queued == ["12345"]


@pytest.mark.asyncio
async def test_already_answered_native_or_receipted_not_replayed(env):
    cfg, store, plugin, histories, calls, queued, sent = env
    histories["11111"] = [msg("11111", 1), msg("67890", 2, age=60)]
    incoming = msg("12345", 3)
    eid = "67890:12345:3"
    store.incoming(eid, "12345", "hello")
    store.set(source_key("67890", "12345", incoming), eid)
    key = f"reply:67890:12345:{store.last_incoming('12345')}"
    store.action(key, "12345", "reply", ["already"])
    store.outcome(key, "succeeded")
    histories["12345"] = [incoming]
    report = await OfflineRecovery(plugin).run()
    assert report["queued"] == 0 and not queued


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["live_message", "pause"])
async def test_live_message_or_pause_during_history_fetch_wins(env, change):
    cfg, store, plugin, histories, calls, queued, sent = env
    histories["12345"] = [msg("12345", 1)]
    original = plugin.bot.call_action

    async def action(name, **args):
        result = await original(name, **args)
        if name == "get_friend_msg_history":
            if change == "live_message":
                store.incoming("new", "12345", "fresh now")
            else:
                cfg.update(paused=True, revision=2)
        return result

    plugin.bot.call_action = action
    report = await OfflineRecovery(plugin).run()
    assert report["queued"] == 0 and not queued
    assert "hello" not in str(store.history("12345"))


@pytest.mark.asyncio
async def test_age_forgotten_and_unknown_outcomes_are_respected(env):
    cfg, store, plugin, histories, calls, queued, sent = env
    histories["12345"] = [msg("12345", 1, age=90000)]
    histories["11111"] = [msg("11111", 2)]
    store.action("uncertain", "11111", "reply", ["maybe sent"])
    store.outcome("uncertain", "unknown")
    report = await OfflineRecovery(plugin).run()
    assert report["queued"] == 0
    histories["12345"] = [msg("12345", 3)]
    store.forget("12345")
    report = await OfflineRecovery(plugin).run()
    assert report["queued"] == 0 and not store.history("12345")


@pytest.mark.asyncio
async def test_newer_offline_input_unlocks_fresh_reply_without_retrying_old_unknown(env):
    cfg, store, plugin, histories, calls, queued, sent = env
    store.incoming("old-in", "12345", "old", now=time.time() - 200)
    store.action("uncertain-old", "12345", "reply", ["old uncertain reply"])
    store.outcome("uncertain-old", "unknown")
    store.db.execute("UPDATE actions SET created=? WHERE id='uncertain-old'", (time.time() - 100,))
    histories["12345"] = [msg("12345", 1, "old", age=200), msg("67890", 2, "old uncertain reply", age=100), msg("12345", 3, "new offline input", age=20)]
    report = await OfflineRecovery(plugin).run()
    await asyncio.gather(*plugin.workers.values())
    assert report["queued"] == 1 and queued == ["12345"]
    assert not store.has_unknown("12345")
    assert await plugin.engine.reply("12345")
    assert len(sent) == 1
    assert not store.action("uncertain-old", "12345", "reply", ["must never retry"])
    assert store.db.execute("SELECT state FROM actions WHERE id='uncertain-old'").fetchone()[0] == "acknowledged_unknown"


@pytest.mark.asyncio
async def test_attachments_recovered_and_dry_run_never_imports(env):
    cfg, store, plugin, histories, calls, queued, sent = env
    histories["12345"] = [
        msg("12345", 1, kind="image"),
        msg("12345", 2, kind="record", age=100),
    ]
    recovery = OfflineRecovery(plugin)
    probe = await recovery.run(read_only=True)
    assert probe["candidates"] == 2 and probe["imported"] == 0
    assert not store.history("12345") and not queued
    report = await recovery.run()
    await asyncio.gather(*plugin.workers.values())
    assert report["queued"] == 1
    assert [
        r[0] for r in store.db.execute("SELECT kind FROM media_inputs ORDER BY created")
    ] == ["image", "voice"]


@pytest.mark.asyncio
async def test_pending_retries_not_reset_and_recovery_obeys_normal_quota(env):
    cfg, store, plugin, histories, calls, queued, sent = env
    histories["11111"] = [msg("11111", 1)]
    plugin.reply_queue.enqueue("11111", "existing")
    plugin.reply_queue.begin("11111")
    await OfflineRecovery(plugin).run()
    assert store.get("reply_job:11111")["attempts"] == 1
    plugin.reply_queue.clear("11111")
    store.db.execute("DELETE FROM state WHERE key LIKE 'offline_cursor:%'")
    for n in range(50):
        store.action(f"seed{n}", "11111", "reply", ["old"])
        store.outcome(f"seed{n}", "succeeded")

    async def drain(contact):
        await plugin.engine.reply(contact)

    plugin.drain = drain
    await OfflineRecovery(plugin).run()
    await asyncio.gather(*plugin.workers.values())
    assert len(sent) == 1 and "今天大肥鱼已经聊满额度" in sent[0][1]
    assert store.status()["calls_today"] == 0
