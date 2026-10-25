import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import social
import qzone_workflow
from engine import Engine, quiet
from policy import QUOTA_FAREWELL, day_start
from social import Social
from store import Store, TZ


@pytest.fixture
def environment(tmp_path, monkeypatch):
    cfg = dict(
        owner_qq="12345",
        bot_qq="67890",
        name="fish",
        persona="natural",
        owner_title="主人",
        revision=1,
        live=True,
        paused=False,
        guests=True,
        guest_mode="open",
        guest_ids=[],
        friend_accept=True,
        friend_daily_limit=0,
        qzone=True,
        qzone_likes=True,
        qzone_friends=True,
        proactive=True,
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
        daily_chat_messages=200,
        guest_daily_messages=20,
        contact_daily_messages={"22222": 30},
    )
    store = Store(tmp_path / "rules.sqlite3")
    sent, generations = [], []

    async def generate(system, contexts, **options):
        generations.append((system, contexts))
        return (
            "hello"
            if options.get("response_mode") == "text"
            else '{"action":"reply","messages":["public draft"],"memory":[]}'
        ), None

    async def send(contact, text):
        sent.append((contact, text))
        return {"message_id": len(sent)}

    engine = Engine(store, generate, send, lambda: dict(cfg))
    monkeypatch.setattr(social, "read_settings", lambda: cfg)
    monkeypatch.setattr(qzone_workflow, "quiet", lambda now: False)
    yield store, cfg, engine, sent, generations
    store.db.close()


def seed(store, contact, count, kind="reply"):
    for index in range(count):
        key = f"seed:{contact}:{kind}:{index}"
        store.action(key, contact, kind, ["old"])
        store.outcome(key, "succeeded")


@pytest.mark.asyncio
@pytest.mark.parametrize("contact,cap", [("11111", 50), ("22222", 100)])
async def test_raised_friend_quotas_allow_last_message_then_stop(environment, contact, cap):
    store, cfg, engine, sent, generations = environment
    cfg.update(guest_daily_messages=50, contact_daily_messages={"22222": 100}, daily_chat_messages=500)
    seed(store, contact, cap - 1)
    store.incoming("last-allowed", contact, "还有一次可以聊")
    assert await engine.reply(contact)
    assert store.sent_count(day_start(), contact) == cap
    store.incoming("now-full", contact, "继续")
    assert await engine.reply(contact)
    assert sent[-1] == (contact, QUOTA_FAREWELL)
    assert len(generations) == 1


@pytest.mark.asyncio
async def test_raised_global_quota_and_notice_use_500(environment):
    store, cfg, engine, sent, generations = environment
    cfg.update(guest_daily_messages=50, daily_chat_messages=500)
    seed(store, "12345", 499, kind="proactive")
    store.incoming("five-hundredth", "12345", "最后一条")
    assert await engine.reply("12345")
    store.incoming("global-full", "12345", "继续")
    assert await engine.reply("12345")
    assert "500" in sent[-1][1] and "200" not in sent[-1][1]
    assert len(generations) == 1


@pytest.mark.asyncio
async def test_twenty_messages_then_exact_farewell_only_once_without_model(environment):
    store, cfg, engine, sent, generations = environment
    seed(store, "11111", 20)
    store.incoming("one", "11111", "hi")
    assert await engine.reply("11111")
    store.incoming("two", "11111", "again")
    assert not await engine.reply("11111")
    assert sent == [("11111", QUOTA_FAREWELL)]
    assert not generations
    assert store.sent_count(day_start(), "11111") == 20


@pytest.mark.asyncio
async def test_selected_contact_gets_thirty_and_owner_has_no_personal_cap(environment):
    store, cfg, engine, sent, generations = environment
    seed(store, "22222", 20)
    store.incoming("one", "22222", "hi")
    assert await engine.reply("22222")
    assert sent[-1] == ("22222", "hello")
    seed(store, "12345", 35)
    store.incoming("owner", "12345", "hi")
    assert await engine.reply("12345")
    assert sent[-1] == ("12345", "hello")


@pytest.mark.asyncio
async def test_global_two_hundred_chat_messages_does_not_call_model(environment):
    store, cfg, engine, sent, generations = environment
    seed(store, "12345", 200)
    store.incoming("event", "12345", "hi")
    assert await engine.reply("12345")
    assert "200" in sent[-1][1]
    assert not generations
    assert store.sent_count(day_start(), kinds=("reply", "proactive")) == 200


@pytest.mark.asyncio
async def test_two_bubble_reply_cannot_cross_personal_message_limit(environment):
    store, cfg, engine, sent, generations = environment
    seed(store, "11111", 19)
    store.incoming("event", "11111", "hi")

    async def generate(*args, **kwargs):
        return '{"action":"reply","messages":["one","two"]}', None

    engine.generate = generate
    assert await engine.reply("11111")
    assert sent == [("11111", "one")]
    assert store.sent_count(day_start(), "11111") == 20


def test_quiet_midnight_to_eight_only():
    now = datetime.now(TZ)
    assert quiet(now.replace(hour=0)) and quiet(now.replace(hour=7))
    assert not quiet(now.replace(hour=8)) and not quiet(now.replace(hour=23))


@pytest.mark.asyncio
async def test_friend_acceptance_has_no_three_person_daily_cap(environment):
    store, cfg, engine, sent, generations = environment
    seed(store, "11111", 3, kind="friend_accept")
    store.db.execute(
        "INSERT INTO friends VALUES(?,?,?,?,?,?)",
        ("request", "22222", "flag", "", "pending", time.time()),
    )

    async def check():
        pass

    async def action(name, **kwargs):
        return [{"user_id": 22222}] if name == "get_friend_list" else None

    st = Social(
        SimpleNamespace(
            store=store, check_account=check, bot=SimpleNamespace(call_action=action)
        )
    )
    await st.friends()
    assert (
        store.db.execute("SELECT state FROM friends WHERE id='request'").fetchone()[0]
        == "accepted"
    )


@pytest.mark.asyncio
async def test_daily_proactive_target_persisted_and_approval_not_counted(
    environment, monkeypatch
):
    import engine as engine_module

    store, cfg, engine, sent, generations = environment
    monkeypatch.setattr(engine_module.random, "randint", lambda a, b: 5)
    now = datetime.now(TZ).replace(hour=20, minute=0)
    monkeypatch.setattr(engine_module, "quiet", lambda moment: False)
    monkeypatch.setattr("store.time.time", lambda: now.timestamp())
    store.incoming("event", "12345", "topic", now.timestamp() - 3 * 3600)
    assert await engine.proactive(now)
    key = f"proactive_target:67890:{now.strftime('%Y-%m-%d')}"
    assert store.get(key) == 5
    assert store.action_count("proactive", day_start(now)) == 1


class Parser:
    @staticmethod
    def parse_feeds(items):
        return [SimpleNamespace(**item) for item in items]

    @staticmethod
    def parse_recent_feeds(data):
        return Parser.parse_feeds(data["items"])


def make_social(environment, monkeypatch):
    store, cfg, engine, sent, generations = environment
    posts = {
        "owner": dict(
            uin=12345,
            tid="owner",
            text="owner public",
            name="owner",
            create_time=int(time.time()),
            comments=[],
        ),
        "friend": dict(
            uin=11111,
            tid="friend",
            text="friend public",
            name="friend",
            create_time=int(time.time()),
            comments=[],
        ),
    }
    writes, likes = [], []

    class API:
        async def get_feeds(self, *args, **kwargs):
            return SimpleNamespace(ok=True, data={"msglist": [posts["owner"]]})

        async def get_recent_feeds(self):
            return SimpleNamespace(ok=True, data={"items": [posts["friend"]]})

        async def get_detail(self, post):
            return SimpleNamespace(ok=True, data=posts[post.tid])

        async def like(self, post):
            likes.append(post.tid)
            return SimpleNamespace(ok=True)

        async def like_state(self, post):
            return post.tid in likes

        async def comment(self, post, content):
            writes.append((post.tid, content))
            posts[post.tid]["comments"].append(
                SimpleNamespace(uin=67890, content=content)
            )
            return SimpleNamespace(ok=True)

    async def check():
        pass

    monkeypatch.setitem(
        sys.modules, "qzone_vendor.core.qzone", SimpleNamespace(QzoneParser=Parser)
    )
    plugin = SimpleNamespace(store=store, engine=engine, check_account=check)
    st = Social(plugin)
    st.qzone_api = API()
    store.set("known_friend_ids", ["11111"])
    return st, posts, writes, likes


@pytest.mark.asyncio
async def test_friend_comment_never_publishes_without_native_owner_approval(
    environment, monkeypatch
):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    token = st.create_approval(
        SimpleNamespace(**posts["friend"]), "exact approved text", cfg
    )
    await st.notify_approvals()
    assert len(sent) == 1 and "exact approved text" in sent[0][1]
    assert store.action_count("proactive", day_start()) == 0
    assert store.sent_count(day_start(), kinds=("reply", "proactive")) == 0
    assert await st.approval_command("11111", f"/同意评论 {token}") is None
    assert not writes
    answer = await st.approval_command("12345", f"/同意评论 {token}")
    assert "已按" in answer
    assert writes == [("friend", "exact approved text")]
    await st.approval_command("12345", f"/同意评论 {token}")
    assert len(writes) == 1
    assert not st.owner_comment_used(cfg)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["reject", "expired", "changed", "forged"])
async def test_approval_reject_expiry_changed_post_and_forgery_never_write(
    environment, monkeypatch, case
):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    post = SimpleNamespace(**posts["friend"])
    token = st.create_approval(post, "draft", cfg)
    if case == "expired":
        store.db.execute("UPDATE qzone_approvals SET expires=0 WHERE id=?", (token,))
    if case == "changed":
        posts["friend"]["text"] = "modified"
    if case == "forged":
        assert (
            await st.publish_comment(post, "different text", cfg, token)
            == "not_approved"
        )
    else:
        command = "拒绝评论" if case == "reject" else "同意评论"
        await st.approval_command("12345", f"/{command} {token}")
    assert not writes


@pytest.mark.asyncio
async def test_scan_likes_owner_and_friend_once_but_friend_only_creates_approval(
    environment, monkeypatch
):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    store.set("qzone_baseline:67890:12345", True)
    store.set("qzone_friend_baseline:67890", True)
    store.action("existing-owner-comment", "qzone:12345", "qzone_comment", ["old"])
    store.outcome("existing-owner-comment", "succeeded")
    await st.qzone()
    assert sorted(likes) == ["friend", "owner"]
    assert not writes
    assert len(sent) == 1 and "public draft" in sent[0][1]
    assert (
        store.db.execute(
            "SELECT count(*) FROM qzone_approvals WHERE state='pending'"
        ).fetchone()[0]
        == 1
    )
    await st.qzone()
    assert len(likes) == 2 and len(sent) == 1


@pytest.mark.asyncio
async def test_uninteresting_posts_never_like_comment_or_request_approval(
    environment, monkeypatch
):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    store.set("qzone_baseline:67890:12345", True)
    store.set("qzone_friend_baseline:67890", True)
    calls = []

    async def decide(contact, kind, instruction, public=False):
        calls.append(kind)
        assert public
        return dict(action="skip", messages=[], revision=cfg["revision"])

    monkeypatch.setattr(engine, "decision", decide)
    await st.qzone()
    await st.qzone(force_latest=True)
    assert calls == ["qzone_interest", "qzone_interest"]
    assert not likes and not writes and not sent
    assert not store.db.execute("SELECT 1 FROM qzone_approvals").fetchone()


@pytest.mark.asyncio
async def test_liking_does_not_force_comment_and_never_sends_interest_reason(
    environment, monkeypatch
):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    store.set("qzone_baseline:67890:12345", True)
    store.set("qzone_friend_baseline:67890", True)

    async def decide(contact, kind, instruction, public=False):
        return dict(
            action="reply" if kind == "qzone_interest" else "skip",
            messages=["internal interest reason"] if kind == "qzone_interest" else [],
            revision=cfg["revision"],
        )

    monkeypatch.setattr(engine, "decision", decide)
    await st.qzone()
    assert sorted(likes) == ["friend", "owner"]
    assert not writes and not sent


@pytest.mark.asyncio
async def test_interest_failure_or_revision_change_never_writes(
    environment, monkeypatch
):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    store.set("qzone_baseline:67890:12345", True)
    store.set("qzone_friend_baseline:67890", True)

    async def decide(contact, kind, instruction, public=False):
        if "owner public" in instruction:
            raise TimeoutError()
        return dict(
            action="reply", messages=["interesting"], revision=cfg["revision"] + 1
        )

    monkeypatch.setattr(engine, "decision", decide)
    await st.qzone()
    assert not likes and not writes and not sent


@pytest.mark.asyncio
async def test_owner_short_approval_prefix_executes_once(environment, monkeypatch):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    monkeypatch.setattr(qzone_workflow.secrets, "token_hex", lambda n: "abcdef12")
    st.create_approval(SimpleNamespace(**posts["friend"]), "exact draft", cfg)
    assert "已按" in await st.approval_command("12345", "同意abcdef")
    await st.approval_command("12345", "同意abcdef")
    assert writes == [("friend", "exact draft")]


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", ["两个都同意", "两个都发", "这两条都发吧", "两条都可以发", "两个都发出去！"])
async def test_bulk_approves_only_two_actually_shown_revised_drafts(environment, monkeypatch, approval):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    tokens = []
    for i in range(3):
        name = f"friend{i}"
        posts[name] = dict(posts["friend"], tid=name, comments=[])
        tokens.append(st.create_approval(SimpleNamespace(**posts[name]), f"old draft {i}", cfg))
    store.message("12345", "user", "前面两个重写")
    store.message("12345", "assistant", f"空间评论待确认 [{tokens[0]}]\n动态正文：test")
    store.message("12345", "assistant", "拟发布的评论：\nreviewed new zero")
    store.message("12345", "assistant", f"同意请发：/同意评论 {tokens[0]}\n\n空间评论待确认 [{tokens[1]}]\n拟发布的评论：\nreviewed new one\n\n同意请发：/同意评论 {tokens[1]}")
    assert await st.approval_command("11111", approval) is None
    answer = await st.approval_command("12345", approval)
    assert answer.count("已按") == 2
    assert writes == [("friend0", "reviewed new zero"), ("friend1", "reviewed new one")]
    assert store.db.execute("SELECT state FROM qzone_approvals WHERE id=?", (tokens[2],)).fetchone()[0] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", ["两个都同意", "两个都发"])
async def test_bulk_two_does_not_guess_which_two_of_three_shown(environment, monkeypatch, approval):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    for i in range(3):
        token = st.create_approval(SimpleNamespace(**posts["friend"]), f"draft {i}", cfg)
        store.message("12345", "assistant", f"空间评论待确认 [{token}]\n拟发布的评论：\ndraft {i}\n同意请发：/同意评论 {token}")
    assert "无法明确" in await st.approval_command("12345", approval)
    assert not writes


@pytest.mark.asyncio
async def test_bulk_send_does_not_use_unshown_or_no_longer_current_drafts(environment, monkeypatch):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    for i in range(2):
        token = st.create_approval(SimpleNamespace(**posts["friend"]), f"draft {i}", cfg)
        store.message("12345", "assistant", f"空间评论待确认 [{token}]\n拟发布的评论：\ndraft {i}\n同意请发：/同意评论 {token}")
    store.message("12345", "user", "先聊点别的")
    store.message("12345", "assistant", "好呀")
    assert "无法明确" in await st.approval_command("12345", "两个都发")
    assert not writes
    assert await st.approval_command("12345", "为什么两个都发") is None


@pytest.mark.asyncio
async def test_compound_approval_and_rewrite_persists_new_drafts(environment, monkeypatch):
    store, cfg, engine, sent, generations = environment
    st, posts, writes, likes = make_social(environment, monkeypatch)
    tokens = []
    for i in range(3):
        name = f"friend{i}"
        posts[name] = dict(posts["friend"], tid=name, comments=[])
        tokens.append(st.create_approval(SimpleNamespace(**posts[name]), f"old {i}", cfg))

    async def rewrite(*args, **kwargs):
        return dict(action="reply", messages=["a better comment"], revision=cfg["revision"])

    monkeypatch.setattr(engine, "decision", rewrite)
    answer = await st.approval_command("12345", f"同意{tokens[2][:6]}，前面两个看看重新写一下咋样")
    assert writes == [("friend2", "old 2")]
    assert answer.count("a better comment") == 2
    assert store.db.execute("SELECT content FROM qzone_approvals WHERE id=?", (tokens[0],)).fetchone()[0] == "a better comment"


def test_social_execution_context_is_only_visible_to_owner(environment):
    from self_activity import social_action_context

    store, cfg, engine, sent, generations = environment
    store.action("qzone_like:11111:post", "qzone:11111", "qzone_like", [])
    store.outcome("qzone_like:11111:post", "unknown", "visibility not confirmed")
    assert "unknown不能说成功" in social_action_context(store, cfg, "12345")
    assert social_action_context(store, cfg, "11111") == ""
