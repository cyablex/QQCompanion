# ruff: noqa: E402
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "astrbot")]
import autopost
from engine import Engine
from public_novelty import is_novel, recent_public
from self_profile import SelfProfile
from sketchbook import Sketchbook
from store import Store, TZ

OLD = "鲸鱼的尾鳍像一枚游动的指纹，每一道缺口和疤痕都能认出不同的个体，偷偷给它建个档案。"
REPEAT = "尾鳍上的纹路像指纹一样，每一头都能被认出来。画着画着，脑子里就有了一册奇怪的鲸鱼档案。"


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = Store(tmp_path / "novelty.sqlite3")
    cfg = dict(owner_qq="11111", bot_qq="22222", name="大肥鱼", persona="喜欢鲸鱼", owner_title="主人", revision=1, live=True, paused=False, autonomous_posts=True, autonomous_drawing=True, autonomous_profile=True, daily_calls=1000, daily_cny=3, monthly_cny=60)
    prompts, writes = [], []
    response = dict(novel=False)

    async def generate(system, contexts, **kwargs):
        prompts.append((system, contexts))
        return json.dumps(response, ensure_ascii=False), None

    async def check():
        pass

    async def action(name, **kwargs):
        writes.append((name, kwargs))
        raise AssertionError("No public write expected")

    engine = Engine(store, generate, None, lambda: dict(cfg))
    engine.sketchbook = Sketchbook(store, tmp_path)
    plugin = SimpleNamespace(engine=engine, store=store, check_account=check, bot=SimpleNamespace(call_action=action))
    monkeypatch.setattr(autopost, "read_settings", lambda: dict(cfg))
    monkeypatch.setattr(autopost, "quiet", lambda _: False)
    store.action("yesterday", "qzone:22222", "qzone_post", [OLD])
    store.outcome("yesterday", "succeeded")
    yield cfg, store, engine, plugin, prompts, writes, response
    store.db.close()


def test_history_survives_deleted_remote_post_and_excludes_private_data(env):
    cfg, store, *_ = env
    store.incoming("secret", "11111", "PRIVATE CHAT")
    store.remember("11111", "PRIVATE MEMORY")
    store.set("sketches:22222:11111", [dict(title="PRIVATE DRAWING")])
    store.action("other-account", "qzone:99999", "qzone_post", ["OTHER ACCOUNT"])
    store.outcome("other-account", "succeeded")
    store.action("failed", "qzone:22222", "qzone_post", ["NEVER SENT"])
    store.outcome("failed", "failed_not_sent")
    rows = recent_public(store, cfg)
    assert rows[0]["text"] == OLD
    assert all(word not in str(rows) for word in ("PRIVATE", "OTHER ACCOUNT", "NEVER SENT"))


@pytest.mark.asyncio
async def test_paraphrase_rejected_but_new_whale_idea_allowed(env):
    cfg, store, engine, _, prompts, _, response = env
    assert not await is_novel(engine, cfg, dict(kind="post", text=REPEAT), recent_public(store, cfg))
    payload = json.loads(prompts[-1][1][0]["content"])
    assert payload["history"][0]["text"] == OLD and payload["candidate"]["text"] == REPEAT
    response["novel"] = True
    assert await is_novel(engine, cfg, dict(kind="drawing", text="鲸鱼把气泡网织成了一顶会漏虾的帽子。"), recent_public(store, cfg))


@pytest.mark.asyncio
async def test_repeated_public_drawing_never_renders_or_requests_post(env):
    cfg, store, engine, _, prompts, _, _ = env

    async def generate(system, contexts, **kwargs):
        prompts.append((system, contexts))
        if "判断自主公开表达" in system:
            return '{"novel":false}', None
        return json.dumps(dict(action="draw", title="鲸尾指纹", caption=REPEAT, background="#ffffff", elements=[dict(type="ellipse", box=[10, 10, 100, 100]) for _ in range(3)])), None

    engine.generate = generate
    assert await engine.sketchbook.create(engine, cfg) is None
    assert not list(engine.sketchbook.root.glob("data/drawings/**/*.png"))
    assert not store.get("own_post_requested")
    assert OLD in prompts[0][0]


@pytest.mark.asyncio
async def test_queued_legacy_drawing_cannot_bypass_post_check_or_retry_on_restart(env):
    cfg, store, engine, plugin, prompts, writes, _ = env
    image = engine.sketchbook.folder(cfg) / "legacy.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (10, 10)).save(image)
    row = dict(id="legacy", title="鲸尾指纹", caption=REPEAT, file=str(image), created=time.time())
    store.set(engine.sketchbook.key(cfg), [row])
    assert await autopost.AutoPosts(plugin).run(force=True) == "duplicate_content"
    assert not writes and len(prompts) == 1
    engine.sketchbook = Sketchbook(store, engine.sketchbook.root)
    assert engine.sketchbook.pending_public(cfg) is None
    assert await autopost.AutoPosts(plugin).run() == "waiting"
    assert len(prompts) == 1
    assert not store.db.execute("SELECT 1 FROM actions WHERE id LIKE 'qzone_post:%'").fetchone()


@pytest.mark.asyncio
async def test_signature_cannot_rephrase_post(env):
    cfg, _, engine, plugin, _, writes, _ = env

    async def generate(system, contexts, **kwargs):
        value = dict(novel=False) if "判断自主公开表达" in system else dict(action="update", signature="鲸尾上的记号是它的身份证。")
        return json.dumps(value), None

    engine.generate = generate
    await SelfProfile(plugin).run(datetime.now(TZ).replace(hour=12))
    assert not writes


@pytest.mark.asyncio
async def test_invalid_review_never_approves_public_content(env):
    cfg, store, engine, _, _, _, response = env
    response.clear()
    with pytest.raises(ValueError, match="novelty"):
        await is_novel(engine, cfg, dict(kind="post", text=REPEAT), recent_public(store, cfg))
