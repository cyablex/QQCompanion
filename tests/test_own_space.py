# ruff: noqa: E402
import copy
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "astrbot")]
import own_space
from engine import Engine
from own_space import OwnSpace
from store import Store


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = dict(
        owner_qq="12345",
        bot_qq="67890",
        name="fish",
        persona="natural",
        owner_title="主人",
        live=True,
        paused=False,
        qzone=True,
        qzone_reply_own=True,
        revision=1,
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
        guests=True,
    )
    store = Store(tmp_path / "space.sqlite3")
    calls, prompts = [], []
    now = int(time.time())
    raw = dict(
        uin=67890,
        tid="post1",
        content="own public post",
        created_time=now - 100,
        commentlist=[
            dict(
                uin=11111,
                name="visitor",
                tid=1,
                content="hello",
                create_time=now - 30,
                list_3=[],
            )
        ],
    )

    async def check():
        pass

    async def generate(system, contexts, **kwargs):
        prompts.append((system, contexts))
        return "你好呀", None

    engine = Engine(store, generate, None, lambda: cfg)

    class Api:
        async def get_feeds(self, *args, **kwargs):
            return SimpleNamespace(
                ok=True, data=dict(msglist=[copy.deepcopy(raw)], total=1)
            )

        async def get_detail(self, post):
            return SimpleNamespace(ok=True, data=copy.deepcopy(raw))

        async def reply(self, post, target, content):
            calls.append((post.tid, target.tid, target.uin, content))
            root = next(c for c in raw["commentlist"] if c["tid"] == target.tid)
            root["list_3"].append(
                dict(
                    uin=67890,
                    name="fish",
                    tid=100 + len(calls),
                    content=content,
                    create_time=int(time.time()),
                )
            )
            return SimpleNamespace(ok=True, data=dict(tid=str(100 + len(calls))))

    api = Api()
    plugin = SimpleNamespace(
        store=store,
        engine=engine,
        check_account=check,
        social=SimpleNamespace(ensure_qzone=check, qzone_api=api),
    )
    monkeypatch.setattr(own_space, "read_settings", lambda: cfg)
    yield cfg, store, raw, api, OwnSpace(plugin), calls, prompts
    store.db.close()


@pytest.mark.asyncio
async def test_scheduled_own_comments_wait_half_hour_across_restart(env):
    cfg, store, raw, api, feature, calls, prompts = env
    assert (await feature.run(scheduled=True))["posts"] == 1
    assert (await feature.run(scheduled=True))["waiting"]
    assert (await OwnSpace(feature.plugin).run(scheduled=True))["waiting"]
    store.set("own_space_scan_at:67890", time.time()-1801)
    assert (await feature.run(scheduled=True))["posts"] == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_root_and_nested_replies_even_same_generated_text_no_private_context(env):
    cfg, store, raw, api, feature, calls, prompts = env
    raw["commentlist"][0]["list_3"] = [
        dict(
            uin=22222,
            name="second",
            tid=2,
            content="hi",
            create_time=int(time.time()) - 10,
        )
    ]
    store.incoming("secret", "12345", "PRIVATE SECRET")
    store.remember("12345", "PRIVATE MEMORY")
    report = await feature.run()
    assert report["replied"] == 2 and len(calls) == 2
    assert calls[1][1:3] == (1, 22222)  # thread root and nested target author
    assert "PRIVATE" not in str(prompts)
    assert store.sent_count(0, kinds=("reply", "proactive")) == 0
    await feature.run()
    assert len(calls) == 2 and len(prompts) == 2  # includes self comments in readback


@pytest.mark.asyncio
async def test_dry_probe_or_foreign_post_never_writes(env):
    cfg, store, raw, api, feature, calls, prompts = env
    report = await feature.run(read_only=True)
    assert report["incoming"] == 1 and not calls and not prompts
    raw["uin"] = 99999
    assert (await feature.run())["posts"] == 0
    assert not calls


@pytest.mark.asyncio
async def test_previous_manual_reply_is_not_repeated(env):
    cfg, store, raw, api, feature, calls, prompts = env
    store.set("own_space_enabled_at:67890", time.time())
    raw["commentlist"][0]["list_3"] = [
        dict(
            uin=67890,
            name="fish",
            tid=5,
            content="already answered",
            create_time=int(time.time()) - 5,
        )
    ]
    await feature.run()
    assert not calls and not prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["pause", "delete", "edit"])
async def test_pause_deleted_or_edited_target_cancels_send(env, change):
    cfg, store, raw, api, feature, calls, prompts = env
    original = feature.plugin.engine.generate

    async def generate(*args, **kwargs):
        result = await original(*args, **kwargs)
        if change == "pause":
            cfg.update(paused=True, revision=2)
        elif change == "delete":
            raw["commentlist"] = []
        else:
            raw["commentlist"][0]["content"] = "changed"
        return result

    feature.plugin.engine.generate = generate
    await feature.run()
    assert not calls


@pytest.mark.asyncio
async def test_budget_failure_remains_pending(env):
    cfg, store, raw, api, feature, calls, prompts = env
    cfg["daily_calls"] = 0
    report = await feature.run()
    assert report["pending"] == 1 and not calls and not prompts
    assert (
        store.db.execute(
            "SELECT count(*) FROM state WHERE key LIKE 'draft:qzone_inbound:%'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_uninteresting_comment_skips_once_without_public_send(env):
    cfg, store, raw, api, feature, calls, prompts = env
    decisions = []

    async def generate(system, contexts, **kwargs):
        decisions.append(system)
        return "[skip]", None

    feature.plugin.engine.generate = generate
    first = await feature.run()
    second = await feature.run()
    assert first["skipped"] == 1 and first["pending"] == 0
    assert second["skipped"] == 0
    assert len(decisions) == 1 and not calls
    assert "大多数" in decisions[0] and "必须回应" not in decisions[0]


@pytest.mark.asyncio
async def test_skip_during_config_change_is_not_committed(env):
    cfg, store, raw, api, feature, calls, prompts = env

    async def generate(*args, **kwargs):
        cfg["revision"] += 1
        return "[skip]", None

    feature.plugin.engine.generate = generate
    await feature.run()
    assert not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'own_space_handled:%'").fetchone()
    assert not calls


@pytest.mark.asyncio
async def test_uncertain_send_not_repeated_and_late_readback_recovers(env):
    cfg, store, raw, api, feature, calls, prompts = env
    original = api.reply

    async def reply(*args):
        await original(*args)
        raise TimeoutError()

    api.reply = reply
    await feature.run()
    assert len(calls) == 1
    row = store.db.execute(
        "SELECT state FROM actions WHERE kind='qzone_incoming_reply'"
    ).fetchone()
    assert row[0] == "unknown"
    await feature.run()
    assert len(calls) == 1 and len(prompts) == 1
    assert (
        store.db.execute(
            "SELECT state FROM actions WHERE kind='qzone_incoming_reply'"
        ).fetchone()[0]
        == "succeeded"
    )
