# ruff: noqa: E402
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import offline_recovery
from offline_recovery import OfflineRecovery, source_key
from reply_queue import ReplyQueue
from social import Social
from store import Store
from test_offline_recovery import msg
from test_social_rules import environment as environment, make_social


async def offered(environment, monkeypatch, count=1):
    store, cfg, engine, sent, generations = environment
    cfg["offline_recovery"] = True
    social, posts, writes, _ = make_social(environment, monkeypatch)
    tokens, history, queued = [], [], []
    for i in range(count):
        name = f"friend{i}"
        posts[name] = dict(posts["friend"], tid=name, comments=[])
        tokens.append(social.create_approval(SimpleNamespace(**posts[name]), f"approved draft {i}", cfg))
    await social.notify_approvals()
    store.db.execute("UPDATE qzone_approvals SET created=?", (time.time() - 600,))
    store.db.execute("UPDATE actions SET created=? WHERE kind='approval_request'", (time.time() - 590,))
    store.db.execute("UPDATE messages SET created=?", (time.time() - 590,))
    for i, token in enumerate(tokens):
        content = store.db.execute("SELECT content FROM actions WHERE id=?", ("approval_request:" + token,)).fetchone()[0]
        history.append(msg(cfg["bot_qq"], 100 + i, "\n".join(json.loads(content)), age=580 - i))

    async def check():
        pass

    async def action(name, **args):
        if name == "get_recent_contact":
            return [dict(peerUin=cfg["owner_qq"], chatType=1, lastestMsg=history[-1])]
        if name == "get_friend_msg_history":
            return dict(messages=history)
        raise AssertionError(name)

    async def drain(contact):
        queued.append(contact)

    plugin = social.plugin
    plugin.social = social
    plugin.workers, plugin.pending = {}, {}
    plugin.bot = SimpleNamespace(call_action=action)
    plugin.reply_queue = ReplyQueue(store)
    plugin.drain = drain
    social.reconcile_friends = check
    monkeypatch.setattr(offline_recovery, "read_settings", lambda: cfg)
    return SimpleNamespace(store=store, cfg=cfg, engine=engine, social=social, plugin=plugin,
                           tokens=tokens, history=history, writes=writes, posts=posts,
                           queued=queued, sent=sent, generations=generations)


@pytest.mark.asyncio
@pytest.mark.parametrize("slash", ["", "/"])
async def test_offline_exact_approvals_execute_even_after_ai_reply_and_cursor(environment, monkeypatch, slash):
    env = await offered(environment, monkeypatch, 2)
    for i, token in enumerate(env.tokens):
        env.history.append(msg(env.cfg["owner_qq"], 200 + i, f"{slash}同意评论 {token}", age=120 - i))
    env.history.append(msg(env.cfg["bot_qq"], 300, "我收到这两条了，但没法执行", age=60))
    env.store.set("offline_cursor:67890:12345", source_key("67890", "12345", env.history[-1]))
    recovery = OfflineRecovery(env.plugin)
    await recovery.run()
    assert env.writes == [("friend0", "approved draft 0"), ("friend1", "approved draft 1")]
    assert not env.queued and not env.generations
    await recovery.run()
    assert len(env.writes) == 2
    assert all(r[0] == "succeeded" for r in env.store.db.execute("SELECT state FROM qzone_authorizations"))


@pytest.mark.asyncio
async def test_offline_bulk_uses_drafts_shown_before_approval_not_later_chat(environment, monkeypatch):
    env = await offered(environment, monkeypatch, 2)
    env.history += [msg("12345", 200, "两个都发", age=120), msg("67890", 201, "好，发出去", age=110)]
    await OfflineRecovery(env.plugin).run()
    assert len(env.writes) == 2 and not env.queued


@pytest.mark.asyncio
async def test_restored_installation_does_not_recover_old_approval_for_unseen_contact(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    env.store.set("migration_restored_at", time.time() - 10)
    result = env.social.comments.capture("12345", f"同意评论 {env.tokens[0]}", "old-event", time.time() - 120)
    assert result == []
    await env.social.comments.resume()
    assert not env.writes
    assert not env.store.db.execute("SELECT 1 FROM qzone_authorizations").fetchone()


@pytest.mark.asyncio
async def test_preflight_failure_survives_new_worker_and_expiry_after_timely_approval(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    source_time = time.time() - 100
    env.store.db.execute("UPDATE qzone_approvals SET expires=? WHERE id=?", (time.time() - 50, token))
    assert env.social.comments.capture("12345", f"同意评论 {token}", "source", source_time) == [token]
    original = env.plugin.check_account

    async def unavailable():
        raise ConnectionError("offline")

    env.plugin.check_account = unavailable
    await env.social.comments.resume()
    assert not env.writes
    assert env.store.db.execute("SELECT state FROM qzone_authorizations").fetchone()[0] == "queued"
    restarted = Social(env.plugin)
    restarted.qzone_api = env.social.qzone_api
    env.plugin.check_account = original
    env.store.db.execute("UPDATE qzone_authorizations SET due=0")
    await restarted.comments.resume()
    assert env.writes == [("friend0", "approved draft 0")]


@pytest.mark.asyncio
async def test_durable_approval_survives_database_close_and_reopen(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    env.social.comments.capture("12345", f"同意评论 {token}", "source", time.time())
    path = env.store.db.execute("PRAGMA database_list").fetchone()[2]
    env.store.db.close()
    reopened = Store(path)
    env.plugin.store = reopened
    env.engine.store = reopened
    restarted = Social(env.plugin)
    restarted.qzone_api = env.social.qzone_api
    try:
        await restarted.comments.resume()
        assert len(env.writes) == 1
        assert reopened.db.execute("SELECT state FROM qzone_authorizations").fetchone()[0] == "succeeded"
    finally:
        reopened.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("arrived", [False, True])
async def test_uncertain_send_only_reads_back_and_never_reposts(environment, monkeypatch, arrived):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    api = env.social.qzone_api
    original = api.comment
    attempts = []

    async def timeout(post, content):
        attempts.append(content)
        if arrived:
            await original(post, content)
        raise TimeoutError("lost receipt")

    api.comment = timeout
    env.social.comments.capture("12345", f"同意评论 {token}", "source", time.time())
    await env.social.comments.resume()
    assert len(attempts) == 1
    env.store.db.execute("UPDATE qzone_authorizations SET due=0")
    await env.social.comments.resume()
    state = env.store.db.execute("SELECT state FROM qzone_authorizations").fetchone()[0]
    assert state == ("succeeded" if arrived else "unknown")
    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_offline_rejection_after_approval_cancels_before_any_public_write(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    env.history += [msg("12345", 200, f"同意评论 {token}", age=120),
                    msg("12345", 201, f"拒绝评论 {token}", age=60)]
    await OfflineRecovery(env.plugin).run()
    assert not env.writes
    assert env.store.db.execute("SELECT state FROM qzone_authorizations").fetchone()[0] == "rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["draft", "post", "forget", "pause", "owner"])
async def test_queued_approval_respects_changes_and_withdrawal(environment, monkeypatch, change):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    env.social.comments.capture("12345", f"同意评论 {token}", "source", time.time())
    if change == "draft":
        env.store.db.execute("UPDATE qzone_approvals SET content='different'")
    elif change == "post":
        env.posts["friend0"]["text"] = "changed public post"
    elif change == "forget":
        env.store.forget("12345")
    elif change == "pause":
        env.cfg["paused"] = True
    else:
        env.cfg["owner_qq"] = "99999"
    await env.social.comments.resume()
    assert not env.writes


@pytest.mark.asyncio
async def test_offline_probe_and_unrelated_admin_commands_never_publish(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    assert env.social.comments.capture("11111", f"同意评论 {token}", "forged", time.time()) is None
    env.history += [msg("12345", 200, f"/同意评论 {token}", age=120), msg("12345", 201, "/暂停", age=60)]
    await OfflineRecovery(env.plugin).run(read_only=True)
    assert not env.writes and not env.store.db.execute("SELECT 1 FROM qzone_authorizations").fetchone()
    await OfflineRecovery(env.plugin).run()
    assert len(env.writes) == 1 and not env.cfg["paused"] and not env.queued


@pytest.mark.asyncio
async def test_live_approval_uses_same_durable_execution_and_returns_actual_result(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    answer = await env.social.approval_command("12345", f"同意评论 {token}", source="live", source_time=time.time())
    assert answer == "" and env.writes == [("friend0", "approved draft 0")]
    assert "读回确认" in env.sent[-1][1]


@pytest.mark.asyncio
async def test_recovery_pages_back_to_approval_before_latest_hundred_messages(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    earlier = env.history + [msg("12345", 200, f"同意评论 {token}", age=120)]
    env.history[:] = [msg("67890", 300, "later reply", age=60)]
    calls = []
    original = env.plugin.bot.call_action

    async def paged(name, **args):
        calls.append(args)
        if name == "get_friend_msg_history" and args.get("message_seq"):
            return dict(messages=earlier)
        return await original(name, **args)

    env.plugin.bot.call_action = paged
    await OfflineRecovery(env.plugin).run()
    assert env.writes == [("friend0", "approved draft 0")]
    assert any(call.get("message_seq") == "300" for call in calls)


@pytest.mark.asyncio
async def test_comment_approvals_recover_when_chat_backfill_is_off_and_chat_worker_busy(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    env.history.append(msg("12345", 200, f"同意评论 {token}", age=120))
    env.cfg["offline_recovery"] = False
    env.plugin.workers["12345"] = object()
    await OfflineRecovery(env.plugin).run()
    assert env.writes == [("friend0", "approved draft 0")]
    assert not env.queued


@pytest.mark.asyncio
async def test_late_approval_and_unseen_changed_draft_are_not_published(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    env.store.db.execute("UPDATE qzone_approvals SET expires=?", (time.time() - 10,))
    assert "已过期" in env.social.comments.capture("12345", f"同意评论 {token}", "late", time.time())
    env.store.db.execute("UPDATE qzone_approvals SET expires=?,content='unseen revision'", (time.time() + 100,))
    assert "不一致" in env.social.comments.capture("12345", f"同意评论 {token}", "changed", time.time())
    assert not env.writes


@pytest.mark.asyncio
async def test_offline_combined_request_recovers_explicit_approval_without_faking_rewrite(environment, monkeypatch):
    env = await offered(environment, monkeypatch)
    token = env.tokens[0]
    env.history.append(msg("12345", 200, f"同意{token[:6]}，前面两个重新写一下", age=120))
    await OfflineRecovery(env.plugin).run()
    assert env.writes == [("friend0", "approved draft 0")]
    assert not env.generations and not env.queued
    assert any("后半句改稿尚未执行" in text for _, text in env.sent)
