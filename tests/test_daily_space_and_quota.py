import asyncio
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import qzone_workflow
import social
from engine import Engine
from policy import contact_limit, day_start
from settings import quota_unlimited
from social import Social
from store import BudgetExceeded, Store, TZ
from web_lookup import WebLookup


def test_temporary_unlimited_expires_at_local_midnight_and_keeps_accounting(tmp_path):
    store = Store(tmp_path / "quota.sqlite3")
    today = datetime.now(TZ)
    cfg = dict(quota_unlimited_day=today.strftime("%Y-%m-%d"), daily_calls=0,
               daily_cny=0, monthly_cny=0)
    rid = store.reserve(cfg, "reply", 1, now=today.timestamp())
    store.settle(rid, 0.25)
    assert store.db.execute("SELECT amount FROM calls").fetchone()[0] == 0.25
    assert quota_unlimited(cfg, today.replace(hour=23, minute=59))
    tomorrow = (today + timedelta(days=1)).replace(hour=0, minute=0)
    assert not quota_unlimited(cfg, tomorrow)
    with pytest.raises(BudgetExceeded):
        store.reserve(cfg, "reply", 1, now=tomorrow.timestamp())
    store.db.close()


@pytest.mark.asyncio
async def test_today_override_allows_private_reply_past_personal_global_and_call_caps(tmp_path):
    store = Store(tmp_path / "reply.sqlite3")
    cfg = dict(owner_qq="11111", bot_qq="22222", live=True, paused=False,
               guests=True, guest_mode="open", revision=1, persona="自然",
               name="大肥鱼", owner_title="主人", guest_daily_messages=0,
               daily_chat_messages=0, daily_calls=0, daily_cny=0, monthly_cny=0,
               quota_unlimited_day=datetime.now(TZ).strftime("%Y-%m-%d"))
    sent = []

    async def generate(*args, **kwargs):
        return "今天可以继续聊。", dict(input_other=1, input_cached=0, output=2)

    async def send(contact, text, **kwargs):
        sent.append(text)
        return dict(message_id=1)

    engine = Engine(store, generate, send, lambda: dict(cfg))
    store.incoming("fresh", "33333", "可以继续吗")
    assert contact_limit(cfg, "33333") is None
    assert await engine.reply("33333")
    assert sent == ["今天可以继续聊。"]
    assert store.sent_count(day_start(), "33333") == 1
    assert store.db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 1
    cfg["quota_unlimited_day"] = "2000-01-01"
    assert contact_limit(cfg, "33333") == 0
    store.db.close()


def test_today_override_includes_web_count_without_changing_default(tmp_path):
    store = Store(tmp_path / "web.sqlite3")
    cfg = dict(daily_calls=0, daily_cny=0, monthly_cny=0, web_daily_requests=0,
               quota_unlimited_day=datetime.now(TZ).strftime("%Y-%m-%d"))
    lookup = WebLookup(store)
    lookup.reserve_request(cfg, "web_search", 0.005)
    cfg["quota_unlimited_day"] = "2000-01-01"
    with pytest.raises(BudgetExceeded):
        lookup.reserve_request(cfg, "web_search", 0.005)
    store.db.close()


@pytest.fixture
def space(tmp_path, monkeypatch):
    store = Store(tmp_path / "space.sqlite3")
    cfg = dict(owner_qq="11111", bot_qq="22222", live=True, paused=False,
               revision=1, qzone=True, qzone_friends=True, qzone_likes=True)
    store.set("known_friend_ids", ["33333"])
    store.set("qzone_baseline:22222:11111", True)
    store.set("qzone_friend_baseline:22222", True)
    pages, requests, decisions, likes, reviews = {}, [], [], [], []
    review_result = dict(description="第一张是阳光下的猫，第二张是窗边的花。", like=True, reason="朋友日常，愿意点赞", comment="")

    async def check():
        pass

    async def feeds(uid, pos=0, num=50):
        requests.append((uid, pos))
        page = pages.get((uid, pos), [])
        if isinstance(page, Exception):
            raise page
        return SimpleNamespace(ok=True, data={"msglist": page})

    async def decide(contact, kind, task, **kwargs):
        decisions.append((kind, task))
        return dict(action="reply" if kind == "qzone_interest" else "skip",
                    messages=["愿意点赞"] if kind == "qzone_interest" else [], revision=cfg["revision"])

    async def review(kind, system, contexts, **kwargs):
        reviews.append((kind, system, contexts))
        return json.dumps(review_result, ensure_ascii=False)

    class Parser:
        @staticmethod
        def parse_feeds(items):
            return items

    monkeypatch.setitem(sys.modules, "qzone_vendor.core.qzone", SimpleNamespace(QzoneParser=Parser))
    monkeypatch.setattr(social, "read_settings", lambda: dict(cfg))
    monkeypatch.setattr(qzone_workflow, "quiet", lambda now: False)
    worker = Social(SimpleNamespace(store=store, check_account=check,
                                   engine=SimpleNamespace(decision=decide, generate_aux=review, send_lock=asyncio.Lock())))
    monkeypatch.setattr(worker, "scan_slot", lambda now: "morning")
    worker.qzone_api = SimpleNamespace(get_feeds=feeds)

    async def like(post):
        likes.append(post.tid)

    async def noop(*args):
        pass

    monkeypatch.setattr(worker, "like_post", like)
    monkeypatch.setattr(worker, "like_own_posts", noop)
    monkeypatch.setattr(worker, "notify_approvals", noop)
    yield SimpleNamespace(store=store, cfg=cfg, worker=worker, pages=pages,
                          requests=requests, decisions=decisions, likes=likes,
                          reviews=reviews, review_result=review_result)
    store.db.close()


def post(tid, created):
    return SimpleNamespace(uin=33333, tid=tid, create_time=created, text="今天的小开心 " + tid,
                           rt_con="", images=[], comments=[])


def test_friend_space_only_scans_morning_and_evening_and_remembers_slot(space):
    space.worker.scan_slot = qzone_workflow.QzoneWorkflow.scan_slot
    day = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    assert space.worker.daily_due(day.replace(hour=9))
    assert not space.worker.daily_due(day.replace(hour=14))
    assert not space.worker.daily_due(day.replace(hour=2))
    space.store.set("qzone_daily:22222", dict(day=day.strftime("%Y-%m-%d"), slot="morning", finished=True))
    assert not space.worker.daily_due(day.replace(hour=10))
    assert space.worker.daily_due(day.replace(hour=19))
    space.store.set("qzone_daily:22222", dict(day=day.strftime("%Y-%m-%d"), slot="evening", finished=True))
    assert not space.worker.daily_due(day.replace(hour=20))


@pytest.mark.asyncio
async def test_daily_view_paginates_all_unread_and_survives_restart(space):
    now = time.time()
    space.store.set("qzone_read_until:22222:33333", now - 3 * 86400)
    space.pages[("33333", 0)] = [post(str(i), now - 2 * 86400 - i) for i in range(50)]
    space.pages[("33333", 50)] = [post("last", now - 2.5 * 86400), post("old", now - 4 * 86400)]
    assert await space.worker.daily_qzone()
    assert len(space.likes) == 51 and "last" in space.likes and "old" not in space.likes
    assert ("33333", 50) in space.requests
    cursor = space.store.get("qzone_read_until:22222:33333")
    assert cursor >= now
    before = len(space.requests), len(space.decisions)
    assert not await space.worker.daily_qzone()
    restarted = Social(space.worker.plugin)
    assert not restarted.daily_due()
    assert before == (len(space.requests), len(space.decisions))


@pytest.mark.asyncio
async def test_next_day_view_only_reads_since_previous_cursor(space):
    now = time.time()
    space.store.set("qzone_read_until:22222:33333", now - 3600)
    space.pages[("33333", 0)] = [post("new", now - 60), post("previous", now - 7200)]
    space.store.set("qzone_daily:22222", dict(day="2000-01-01", finished=True))
    assert await space.worker.daily_qzone()
    assert space.likes == ["new"]
    assert not space.decisions and len(space.reviews) == 1


@pytest.mark.asyncio
async def test_unread_failure_preserves_friend_cursor_and_continues_other_friends(space):
    now = time.time()
    space.store.set("known_friend_ids", ["33333", "44444"])
    space.store.set("qzone_read_until:22222:33333", now - 2 * 86400)
    space.pages[("33333", 0)] = OSError("temporarily unavailable")
    assert await space.worker.daily_qzone()
    assert space.store.get("qzone_read_until:22222:33333") == now - 2 * 86400
    assert space.store.get("qzone_read_until:22222:44444") >= now
    assert space.store.get("qzone_daily:22222")["errors"] == {"33333": "OSError"}
    assert space.store.get("qzone_read_until:22222:11111") >= now


@pytest.mark.asyncio
async def test_model_failure_preserves_unread_cursor_and_retries_on_next_day(space):
    now = time.time()
    space.store.set("qzone_read_until:22222:33333", now - 2 * 86400)
    space.pages[("33333", 0)] = [post("pending", now - 1.5 * 86400)]
    original = space.worker.plugin.engine.generate_aux

    async def fail(*args, **kwargs):
        raise TimeoutError()

    space.worker.plugin.engine.generate_aux = fail
    assert await space.worker.daily_qzone()
    assert not space.likes
    assert space.store.get("qzone_read_until:22222:33333") == now - 2 * 86400
    assert not space.worker.daily_due()
    space.store.set("qzone_daily:22222", dict(day="2000-01-01", finished=True))
    previous = space.store.get("qzone_interest:33333:pending")
    previous["retry_after"] = 0
    space.store.set("qzone_interest:33333:pending", previous)
    space.worker.plugin.engine.generate_aux = original
    assert await space.worker.daily_qzone()
    assert space.likes == ["pending"]


@pytest.mark.asyncio
async def test_old_unread_comment_approval_is_valid_from_time_of_review(space):
    now = time.time()
    space.store.set("qzone_read_until:22222:33333", now - 3 * 86400)
    space.pages[("33333", 0)] = [post("older", now - 2 * 86400)]

    space.review_result["comment"] = "这幅画的颜色很舒服。"
    assert await space.worker.daily_qzone()
    approval = space.store.db.execute("SELECT state, expires FROM qzone_approvals").fetchone()
    assert approval["state"] == "pending" and approval["expires"] > now + 23 * 3600


@pytest.mark.asyncio
async def test_repeated_page_cannot_falsely_advance_unread_cursor(space):
    now = time.time()
    space.store.set("qzone_read_until:22222:33333", now - 86400)
    page = [post(str(i), now - 60) for i in range(50)]
    space.pages[("33333", 0)] = page
    space.pages[("33333", 50)] = page
    assert await space.worker.daily_qzone()
    assert not space.likes
    assert space.store.get("qzone_read_until:22222:33333") == now - 86400
    assert space.store.get("qzone_daily:22222")["errors"] == {"33333": "RuntimeError"}


@pytest.mark.asyncio
async def test_combined_review_reads_real_images_likes_and_drafts_in_one_call(space, monkeypatch):
    space.cfg["image_understanding"] = True
    p = post("photos", time.time() - 60)
    p.images = [f"https://qpic.cn/{i}.jpg" for i in range(5)]
    space.pages[("33333", 0)] = [p]
    space.store.set("private_image_caption", "PRIVATE_CHAT_SECRET")
    downloaded = []

    def download(url):
        downloaded.append(url)
        return b"real-photo-input"

    monkeypatch.setattr(qzone_workflow, "download_qq_media", download)
    monkeypatch.setattr(qzone_workflow, "image_frames", lambda data: ["data:image/jpeg;base64,FRAME"])
    space.review_result["comment"] = "猫猫选了个好地方晒太阳。"
    assert await space.worker.daily_qzone()
    assert len(downloaded) == 2 and len(space.reviews) == 1
    assert space.reviews[0][0] == "qzone_review" and not space.decisions
    assert "PRIVATE_CHAT_SECRET" not in str(space.reviews)
    assert len([x for x in space.reviews[0][2][0]["content"] if x["type"] == "image_url"]) == 2
    assert space.likes == ["photos"]
    assert space.store.db.execute("SELECT state FROM qzone_approvals").fetchone()[0] == "pending"
    assert space.store.get(space.worker.observation_key(p, space.cfg))["read_count"] == 2
    assert await space.worker.review_post(p, space.cfg)
    assert len(space.reviews) == 1 and len(downloaded) == 2


@pytest.mark.asyncio
async def test_combined_review_can_like_without_comment_and_skip_without_writes(space):
    p = post("like-only", time.time() - 60)
    space.pages[("33333", 0)] = [p]
    assert await space.worker.daily_qzone()
    assert space.likes == ["like-only"] and len(space.reviews) == 1
    assert not space.decisions
    assert not space.store.db.execute("SELECT 1 FROM qzone_approvals").fetchone()
    space.review_result.update(like=False, comment="这条不应该发")
    assert not await space.worker.review_post(post("skip", time.time()), space.cfg)
    assert space.store.get("qzone_interest:33333:skip")["comment"] == ""


@pytest.mark.asyncio
async def test_combined_review_discards_stale_result(space):
    async def changed(*args, **kwargs):
        space.cfg["revision"] += 1
        return json.dumps(space.review_result, ensure_ascii=False)

    space.worker.plugin.engine.generate_aux = changed
    p = post("stale", time.time())
    assert not await space.worker.review_post(p, space.cfg)
    assert space.store.get("qzone_interest:33333:stale")["state"] == "pending"
    assert not space.likes
