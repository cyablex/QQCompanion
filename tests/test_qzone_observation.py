import asyncio
import hashlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import qzone_workflow
import social
from social import Social
from store import Store


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = Store(tmp_path / "qzone.sqlite3")
    cfg = dict(owner_qq="12345", bot_qq="67890", live=True, paused=False,
               revision=1, qzone=True, qzone_friends=True, qzone_likes=True,
               image_understanding=True)
    decisions, vision, downloads, likes, notifications = [], [], [], [], []
    posts = [SimpleNamespace(uin=11111, tid="photos", text="", rt_con="",
                             images=[f"http://qpic.cn/{i}.jpg" for i in range(9)],
                             create_time=time.time(), comments=[])]
    store.set("known_friend_ids", ["11111"])
    store.set("qzone_baseline:67890:12345", True)
    store.set("qzone_friend_baseline:67890", True)

    async def decide(contact, kind, instruction, public=False):
        decisions.append((kind, instruction, public))
        return dict(action="reply" if kind == "qzone_interest" else "skip",
                    messages=["朋友在分享食物和观影近况"] if kind == "qzone_interest" else [],
                    revision=cfg["revision"])

    async def observe(kind, system, contexts, **kwargs):
        vision.append((kind, system, contexts, kwargs))
        return "第一张是桌上的点心，第二张是影视画面。"

    async def check():
        pass

    async def feeds(*args, **kwargs):
        return SimpleNamespace(ok=True, data={"msglist": []})

    async def recent():
        return SimpleNamespace(ok=True, data={"items": posts})

    async def like(post):
        likes.append(post.tid)
        return SimpleNamespace(ok=True)

    async def like_state(post):
        return post.tid in likes

    class Parser:
        @staticmethod
        def parse_feeds(items):
            return items

        @staticmethod
        def parse_recent_feeds(data):
            return data["items"]

    monkeypatch.setitem(sys.modules, "qzone_vendor.core.qzone", SimpleNamespace(QzoneParser=Parser))
    monkeypatch.setattr(social, "read_settings", lambda: dict(cfg))

    def download(url):
        downloads.append(url)
        return b"photo"

    monkeypatch.setattr(qzone_workflow, "download_qq_media", download)
    monkeypatch.setattr(qzone_workflow, "image_frames", lambda data: ["data:image/jpeg;base64,FRAME"])
    engine = SimpleNamespace(decision=decide, generate_aux=observe, send_lock=asyncio.Lock())
    worker = Social(SimpleNamespace(store=store, engine=engine, check_account=check))
    worker.qzone_api = SimpleNamespace(get_feeds=feeds, get_recent_feeds=recent, like=like, like_state=like_state)

    async def notify():
        notifications.append("notified")

    monkeypatch.setattr(worker, "notify_approvals", notify)
    yield SimpleNamespace(store=store, cfg=cfg, worker=worker, posts=posts,
                          decisions=decisions, vision=vision, downloads=downloads,
                          likes=likes, engine=engine)
    store.db.close()


@pytest.mark.asyncio
async def test_image_only_post_uses_two_real_images_and_likes_without_comment(env):
    env.store.set("image_caption:private", "PRIVATE_CHAT_CAPTION")
    await env.worker.qzone()
    assert env.downloads == ["https://qpic.cn/0.jpg", "https://qpic.cn/1.jpg"]
    assert len(env.vision) == 1 and env.vision[0][0] == "qzone_image_understanding"
    assert len([p for p in env.vision[0][2][0]["content"] if p["type"] == "image_url"]) == 2
    assert "PRIVATE_CHAT_CAPTION" not in str(env.vision) + str(env.decisions)
    assert env.likes == ["photos"]
    assert "只读取其中2张" in env.decisions[0][1]
    assert "第一张是桌上的点心" in env.decisions[0][1]
    assert "正常好友动态以点赞为默认" in env.decisions[0][1]
    assert "第一张是桌上的点心" in env.decisions[1][1]
    assert not env.store.db.execute("SELECT 1 FROM qzone_approvals").fetchone()
    await env.worker.qzone()
    assert len(env.vision) == 1 and env.likes == ["photos"]


@pytest.mark.asyncio
async def test_actual_legacy_skip_reassessed_once_without_replaying_baseline(env):
    post = env.posts[0]
    env.store.set(f"qzone_seen:{post.uin}:{post.tid}", True)
    env.store.set(f"qzone_interest:{post.uin}:{post.tid}",
                  dict(text_hash=hashlib.sha256(post.text.encode()).hexdigest(), state="decided", interested=False))
    env.posts.append(SimpleNamespace(uin=11111, tid="baseline", text="old baseline",
                                     images=[], create_time=time.time(), comments=[]))
    env.store.set("qzone_seen:11111:baseline", True)
    await env.worker.qzone()
    await env.worker.qzone()
    assert env.likes == ["photos"]
    assert len(env.vision) == 1


@pytest.mark.asyncio
async def test_seen_post_recovers_after_model_failure_and_one_hour_backoff(env, monkeypatch):
    now = time.time()
    monkeypatch.setattr(qzone_workflow.time, "time", lambda: now)
    original = env.engine.generate_aux

    async def fail(*args, **kwargs):
        raise TimeoutError()

    env.engine.generate_aux = fail
    await env.worker.qzone()
    assert not env.likes
    assert env.store.get("qzone_interest:11111:photos")["state"] == "pending"
    download_count = len(env.downloads)
    env.engine.generate_aux = original
    await env.worker.qzone()
    assert len(env.downloads) == download_count and not env.likes
    now += 3601
    await env.worker.qzone()
    assert env.likes == ["photos"]


@pytest.mark.asyncio
async def test_download_failure_does_not_judge_from_imagined_pictures(env, monkeypatch):
    def fail(url):
        raise OSError()

    monkeypatch.setattr(qzone_workflow, "download_qq_media", fail)
    await env.worker.qzone()
    assert not env.vision and not env.decisions and not env.likes
    assert env.store.get("qzone_interest:11111:photos")["state"] == "pending"


@pytest.mark.asyncio
async def test_disabling_image_understanding_does_not_download_or_reuse_old_caption(env):
    await env.worker.observe_post_images(env.posts[0], env.cfg)
    env.cfg["image_understanding"] = False
    env.vision.clear()
    env.downloads.clear()

    async def decide(contact, kind, instruction, public=False):
        env.decisions.append((kind, instruction, public))
        return dict(action="skip", messages=["未读到图片"], revision=env.cfg["revision"])

    env.engine.decision = decide
    await env.worker.qzone()
    assert not env.vision and not env.downloads and not env.likes
    assert "未读到图像内容" in env.decisions[0][1]
    assert "第一张是桌上的点心" not in env.decisions[0][1]
    assert env.store.get("qzone_interest:11111:photos")["reason"] == "未读到图片"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["pause", "revision"])
async def test_changed_config_discards_observation_and_never_likes(env, change):
    async def observe(*args, **kwargs):
        if change == "pause":
            env.cfg["paused"] = True
        else:
            env.cfg["revision"] += 1
        return "a completed but stale observation"

    env.engine.generate_aux = observe
    await env.worker.qzone()
    assert not env.likes and not env.decisions
    assert env.store.get(env.worker.observation_key(env.posts[0], env.cfg))["state"] == "pending"


@pytest.mark.asyncio
async def test_ambiguous_prior_like_is_not_retried_after_interest_upgrade(env):
    env.store.action("qzone_like:11111:photos", "qzone:11111", "qzone_like", [])
    env.store.outcome("qzone_like:11111:photos", "unknown")
    async def unavailable(post):
        return None
    env.worker.qzone_api.like_state = unavailable
    await env.worker.qzone()
    assert not env.likes


@pytest.mark.asyncio
async def test_failed_comment_does_not_stop_next_friends_like(env):
    import json

    original = env.engine.decision

    async def decide(contact, kind, instruction, public=False):
        if kind == "qzone_comment":
            raise json.JSONDecodeError("invalid draft", "", 0)
        return await original(contact, kind, instruction, public=public)

    env.engine.decision = decide
    env.posts.append(SimpleNamespace(uin=11111, tid="next-post", text="今天做了蛋糕",
                                     images=[], rt_con="", create_time=time.time(), comments=[]))
    await env.worker.qzone()
    assert env.likes == ["photos", "next-post"]
    assert env.store.get("qzone_comment_error") == "JSONDecodeError"
    await env.worker.qzone()
    assert env.likes == ["photos", "next-post"]


@pytest.mark.asyncio
async def test_like_acknowledgement_without_readback_is_unknown_and_not_retried(env):
    async def not_visible(post):
        return False

    env.worker.qzone_api.like_state = not_visible
    assert await env.worker.like_post(env.posts[0]) == "unknown"
    assert await env.worker.like_post(env.posts[0]) == "unknown"
    assert env.likes == ["photos"]


@pytest.mark.asyncio
async def test_legacy_success_repairs_only_when_actual_canonical_page_is_unliked(env):
    key = "qzone_like:11111:photos"
    env.store.action(key, "qzone:11111", "qzone_like", [])
    env.store.outcome(key, "succeeded")
    assert await env.worker.like_post(env.posts[0]) == "succeeded"
    assert env.likes == ["photos"]
    assert "readback" in env.store.db.execute("SELECT result FROM actions WHERE id=?", (key,)).fetchone()[0]
    await env.worker.like_post(env.posts[0])
    assert env.likes == ["photos"]


@pytest.mark.asyncio
async def test_all_own_posts_get_self_likes_once_without_interest_or_comments(env):
    own = [SimpleNamespace(uin=67890, tid=f"mine-{i}") for i in range(3)]

    async def feeds(*args, **kwargs):
        return SimpleNamespace(ok=True, data={"msglist": own + [env.posts[0]]})

    env.worker.qzone_api.get_feeds = feeds
    await env.worker.like_own_posts(env.cfg)
    await env.worker.like_own_posts(env.cfg)
    assert env.likes == ["mine-0", "mine-1", "mine-2"]
    assert not env.vision and not env.decisions
    assert env.store.db.execute("SELECT count(*) FROM actions WHERE kind='qzone_self_like' AND state='succeeded'").fetchone()[0] == 3
