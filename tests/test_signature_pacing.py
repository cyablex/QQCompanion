import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import self_profile
from self_profile import SelfProfile, SIGNATURE_MIN_AGE, SIGNATURE_POLICY_VERSION
from store import Store, TZ


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = Store(tmp_path / "profile.sqlite3")
    cfg = dict(owner_qq="12345", bot_qq="67890", name="大肥鱼", persona="开朗，喜欢海洋和白米饭",
               revision=1, live=True, paused=False, autonomous_profile=True)
    calls, writes = [], []
    response = dict(action="update", signature="心里有片海，日子慢慢来。")
    remote = dict(signature="刚读到鲸鱼捕食的新知识，明天继续。", status=(10, 1059))

    async def generate(kind, system, contexts, **kwargs):
        calls.append((kind, system, contexts))
        if kind == "self_signature":
            return json.dumps(response, ensure_ascii=False)
        if kind == "public_novelty":
            return '{"novel":true}'
        return json.dumps(dict(action="update", status="元气满满", signature="今天刚读完一篇文章"))

    async def action(name, **kwargs):
        writes.append(name)
        if name == "set_self_longnick":
            remote["signature"] = kwargs["longNick"]
        elif name == "get_stranger_info":
            return dict(long_nick=remote["signature"])
        elif name == "set_online_status":
            remote["status"] = (kwargs["status"], kwargs["ext_status"])
        elif name == "nc_get_user_status":
            return dict(status=remote["status"][0], ext_status=remote["status"][1])

    async def nothing(*args):
        pass

    monkeypatch.setattr(self_profile.asyncio, "sleep", nothing)
    engine = SimpleNamespace(config_reader=lambda: dict(cfg), send_lock=asyncio.Lock(), generate_aux=generate,
                             activities=SimpleNamespace(context=lambda *args, **kwargs: "PUBLIC_READING_PROGRESS"),
                             sketchbook=SimpleNamespace(context=lambda *args, **kwargs: "PUBLIC_NEW_PICTURE"))
    worker = SelfProfile(SimpleNamespace(store=store, engine=engine, check_account=nothing,
                                         bot=SimpleNamespace(call_action=action)))
    now = datetime.now(TZ).replace(hour=12)
    yield SimpleNamespace(store=store, cfg=cfg, engine=engine, worker=worker, calls=calls,
                          writes=writes, remote=remote, response=response, now=now,
                          key=worker.key(cfg), stamp=now.timestamp())
    store.db.close()


def stable(env):
    env.store.set(env.key, dict(signature=env.response["signature"], signature_confirmed_at=env.stamp,
                                signature_stable_since=env.stamp, signature_policy_version=SIGNATURE_POLICY_VERSION))


@pytest.mark.asyncio
async def test_news_style_migration_changes_once_despite_recent_old_policy_write(env):
    env.store.set(env.key, dict(signature=env.remote["signature"], signature_attempt=env.stamp - 60,
                                signature_confirmed_at=env.stamp - 60))
    await env.worker.review_signature(env.cfg, env.stamp)
    assert env.remote["signature"] == env.response["signature"]
    assert env.store.get(env.key)["signature_policy_version"] == SIGNATURE_POLICY_VERSION
    await env.worker.review_signature(env.cfg, env.stamp + 4 * 3600)
    assert env.writes.count("set_self_longnick") == 1


@pytest.mark.asyncio
async def test_thirty_day_hold_avoids_model_calls_and_cannot_be_bypassed_by_writer(env):
    stable(env)
    for days in (1, 7, 29):
        await env.worker.review_signature(env.cfg, env.stamp + days * 86400)
    assert not env.calls and not env.writes
    assert not await env.worker.write(env.cfg, "signature", "另一句签名", env.stamp + SIGNATURE_MIN_AGE - 1)
    assert not env.writes


@pytest.mark.asyncio
async def test_expiry_allows_keep_and_reviews_at_most_once_a_week(env):
    stable(env)
    env.response.clear()
    env.response.update(action="keep")
    first = env.stamp + 31 * 86400
    await env.worker.review_signature(env.cfg, first)
    await env.worker.review_signature(env.cfg, first + 86400)
    assert len(env.calls) == 1 and not env.writes
    await env.worker.review_signature(env.cfg, first + 7 * 86400)
    assert len(env.calls) == 2 and not env.writes


@pytest.mark.asyncio
async def test_signature_prompt_excludes_daily_activity_and_private_material(env):
    env.store.incoming("private", "12345", "PRIVATE_CHAT_SECRET")
    await env.worker.review_signature(env.cfg, env.stamp)
    prompt = next(c for c in env.calls if c[0] == "self_signature")
    assert "PUBLIC_READING_PROGRESS" not in str(prompt)
    assert "PUBLIC_NEW_PICTURE" not in str(prompt)
    assert "PRIVATE_CHAT_SECRET" not in str(prompt)
    assert "默认keep" in prompt[1] and "长期" in prompt[1]


@pytest.mark.asyncio
async def test_status_changes_do_not_change_signature_even_if_model_returns_both(env):
    stable(env)
    original = env.store.get(env.key)["signature"]
    await env.worker.run(env.now)
    assert env.writes.count("set_online_status") == 1
    assert "set_self_longnick" not in env.writes
    assert env.store.get(env.key)["signature"] == original
    assert all(c[0] != "self_signature" for c in env.calls)


@pytest.mark.asyncio
async def test_pause_during_signature_generation_prevents_write(env):
    async def generate(*args, **kwargs):
        env.cfg["paused"] = True
        return json.dumps(env.response)

    env.engine.generate_aux = generate
    await env.worker.review_signature(env.cfg, env.stamp)
    assert not env.writes


@pytest.mark.asyncio
async def test_failed_write_does_not_start_thirty_day_retention_or_retry_immediately(env):
    async def fail(*args, **kwargs):
        env.writes.append("failed")
        raise TimeoutError()

    env.worker.plugin.bot.call_action = fail
    assert not await env.worker.write(env.cfg, "signature", "一条长期签名", env.stamp)
    assert not await env.worker.write(env.cfg, "signature", "一条长期签名", env.stamp + 3600)
    assert env.writes == ["failed"]
    assert env.store.get(env.key).get("signature_confirmed_at") is None
