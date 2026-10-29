import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import owner_sharing
from owner_sharing import OwnerSharing
from store import Store


@pytest.fixture
def sharing(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_sharing, "ROOT", tmp_path)
    store = Store(tmp_path / "share.sqlite3")
    cfg = dict(owner_qq="11111", bot_qq="22222", owner_public_sharing=True)
    store.incoming("owner-image", "11111", "这只小猫坐得像个小面包，好好笑")
    source = store.db.execute("SELECT MAX(id) FROM messages").fetchone()[0]
    store.incoming("friend-secret", "33333", "FRIEND_PRIVATE_MATERIAL")
    feature = OwnerSharing(store)
    response = dict(action="share", privacy_safe=True, public_text="主人给我看了只面包一样的小猫，越看越觉得可以当猫猫店长。",
                    source_ids=[source], share_image=True, image_class="animal")
    prompts = []
    async def review(kind, system, contexts):
        prompts.append((system, contexts))
        return json.dumps(response, ensure_ascii=False)
    engine = SimpleNamespace(generate_aux=review, memory_epoch=lambda contact: 0)
    yield store, cfg, feature, source, response, prompts, engine
    store.db.close()


def image_data():
    raw=io.BytesIO()
    Image.new("RGB", (100, 100), "blue").save(raw, format="PNG")
    return raw.getvalue()


@pytest.mark.asyncio
async def test_only_owner_material_and_approved_image_can_enter_public_candidate(sharing):
    store, cfg, feature, source, response, prompts, engine = sharing
    feature.stage_image(cfg, "11111", "owner-image", image_data())
    candidate = await feature.candidate(engine, cfg)
    assert candidate and candidate["image"] and Path(candidate["image"]["file"]).is_file()
    assert "FRIEND_PRIVATE_MATERIAL" not in str(prompts)
    feature.used(candidate, "post-id")
    assert await feature.candidate(engine, cfg) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unsafe", "private_text", "foreign_source", "unreviewed_image", "disabled", "veto"])
async def test_private_or_unapproved_candidates_are_not_exported(sharing, change):
    store, cfg, feature, source, response, prompts, engine = sharing
    feature.stage_image(cfg, "11111", "owner-image", image_data())
    if change == "unsafe":
        response["privacy_safe"] = False
    elif change == "private_text":
        response["public_text"] = "主人的账号是1234567"
    elif change == "foreign_source":
        response["source_ids"] = [source + 1]
    elif change == "unreviewed_image":
        response["image_class"] = "unsafe"
    elif change == "disabled":
        cfg["owner_public_sharing"] = False
    elif change == "veto":
        store.incoming("veto", "11111", "这张图别发，保密")
    result = await feature.candidate(engine, cfg)
    if change == "unreviewed_image":
        assert result and result["image"] is None
    else:
        assert result is None


def test_forget_invalidates_staged_owner_image_before_message_ids_can_be_reused(sharing):
    store, cfg, feature, source, response, prompts, engine = sharing
    feature.stage_image(cfg, "33333", "friend-secret", image_data())
    assert not store.get("owner_share_image:friend-secret")
    feature.stage_image(cfg, "11111", "owner-image", image_data())
    assert store.get("owner_share_image:owner-image")
    store.forget("11111")
    assert store.get("owner_share_image:owner-image") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("forget_during_generation", [False, True])
async def test_publication_uses_reviewed_owner_image_and_forget_cancels_it(sharing, monkeypatch, forget_during_generation):
    import autopost
    from engine import Engine

    store, cfg, feature, source, response, prompts, reviewer = sharing
    cfg.update(name="大肥鱼", persona="开朗", owner_title="主人", live=True, paused=False,
               guests=False, revision=1, autonomous_posts=True, post_requires_approval=False, autonomous_drawing=False,
               daily_calls=1000, daily_cny=3, monthly_cny=60)
    feature.stage_image(cfg, "11111", "owner-image", image_data())
    writes=[]
    async def generate(system, contexts, **kwargs):
        assert "FRIEND_PRIVATE_MATERIAL" not in str((system, contexts))
        assert "唯一获准公开的主人趣事摘要" in str(contexts)
        if forget_during_generation:
            store.forget("11111")
        return json.dumps(dict(action="reply", messages=[response["public_text"]], share_owner_chat=True), ensure_ascii=False), None
    engine=Engine(store, generate, None, lambda: dict(cfg))
    engine.generate_aux=reviewer.generate_aux
    async def noop(*args, **kwargs): pass
    async def send(action, **kwargs):
        writes.append((action, kwargs))
        return dict(tid="test-post")
    async def feeds(*args, **kwargs):
        return SimpleNamespace(ok=True, data=dict(msglist=[SimpleNamespace(tid="test-post", text=writes[-1][1]["content"])]))
    plugin=SimpleNamespace(store=store, engine=engine, check_account=noop,
                           bot=SimpleNamespace(call_action=send),
                           social=SimpleNamespace(ensure_qzone=noop, like_post=noop, qzone_api=SimpleNamespace(get_feeds=feeds)))
    monkeypatch.setattr(autopost, "read_settings", lambda: dict(cfg))
    monkeypatch.setattr(autopost, "quiet", lambda now: False)
    monkeypatch.setitem(sys.modules, "qzone_vendor.core.qzone", SimpleNamespace(QzoneParser=SimpleNamespace(parse_feeds=lambda items: items)))
    async def worthy(*args):
        return dict(worthy=True, reason="单独验证分享授权")
    monkeypatch.setattr(autopost.AutoPosts, "sharing_review", worthy)
    status=await autopost.AutoPosts(plugin).run()
    if forget_during_generation:
        assert status == "cancelled" and not writes
    else:
        assert status == "succeeded" and len(writes) == 1
        assert writes[0][0] == "send_qzone_msg" and writes[0][1]["ugc_right"] == 4
        assert len(writes[0][1]["images"]) == 1
