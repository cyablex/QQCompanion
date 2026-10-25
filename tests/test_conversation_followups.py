import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import conversation_followups as workflow_module
import engine as engine_module
from conversation_followups import ConversationFollowups
from engine import Engine
from policy import day_start
from store import Store, TZ


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = dict(
        owner_qq="11111",
        bot_qq="22222",
        name="fish",
        persona="natural",
        owner_title="主人",
        revision=1,
        live=True,
        paused=False,
        guests=True,
        guest_mode="open",
        proactive=True,
        friend_proactive=True,
        conversation_followups=True,
        daily_calls=400,
        daily_cny=3,
        monthly_cny=60,
        daily_chat_messages=200,
        guest_daily_messages=20,
        proactive_min=1,
        proactive_max=5,
        friend_proactive_daily_max=2,
        digital_activities=False,
        memory_enabled=True,
    )
    now = (
        datetime.now(TZ).replace(hour=14, minute=0, second=0, microsecond=0).timestamp()
    )
    monkeypatch.setattr(workflow_module.time, "time", lambda: now)
    monkeypatch.setattr(engine_module, "quiet", lambda moment: False)
    store = Store(tmp_path / "followups.sqlite3")
    store.set("known_friend_ids", ["33333"])
    calls, sent = [], []

    async def generate(system, contexts, **options):
        calls.append((system, contexts))
        return (
            '{"action":"reply","messages":["刚才那点补充：鲸尾的纹路可以用来辨认个体。"],"memory":[]}',
            None,
        )

    async def send(contact, text):
        sent.append((contact, text))
        return {"message_id": str(len(sent))}

    engine = Engine(store, generate, send, lambda: cfg)
    plugin = SimpleNamespace(store=store, engine=engine, workers={})
    workflow = ConversationFollowups(plugin)

    def seed(
        contact="33333",
        text="这点我还没说完，稍后补充一下鲸尾的事",
        due=None,
        expires=None,
    ):
        store.incoming(
            "seed-" + contact, contact, "刚才说的鲸鱼是什么情况", now=now - 600
        )
        store.message(contact, "assistant", text, now=now - 580)
        row = store.db.execute(
            "SELECT id FROM messages WHERE contact=? ORDER BY id DESC LIMIT 1",
            (contact,),
        ).fetchone()
        cursor = store.db.execute(
            "INSERT INTO cm_followups(contact,source_id,evidence,reason,due,expires,state,created) VALUES(?,?,?,?,?,?,'pending',?)",
            (
                contact,
                row["id"],
                text,
                text,
                now - 1 if due is None else due,
                now + 600 if expires is None else expires,
                now - 580,
            ),
        )
        return cursor.lastrowid

    def state(task_id):
        return store.db.execute(
            "SELECT state FROM cm_followups WHERE id=?", (task_id,)
        ).fetchone()[0]

    yield SimpleNamespace(
        cfg=cfg,
        now=now,
        store=store,
        engine=engine,
        plugin=plugin,
        workflow=workflow,
        calls=calls,
        sent=sent,
        seed=seed,
        state=state,
    )
    store.db.close()


@pytest.mark.asyncio
async def test_due_continuation_regenerates_and_receipt_counts_as_proactive(env):
    task = env.seed()
    assert await env.workflow.run(env.now)
    assert len(env.calls) == len(env.sent) == 1
    assert env.sent[0][0] == "33333"
    assert "稍后补充一下鲸尾" in str(env.calls)
    assert env.state(task) == "completed"
    assert env.store.action_count("friend_proactive", day_start()) == 1
    assert not await env.workflow.run(env.now)
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_contacts_get_only_their_own_evidence_and_memory(env):
    env.seed()
    env.seed("44444", "OTHER_CONTACT_PRIVATE 稍后补充另一件事")
    env.store.remember("44444", "OTHER_CONTACT_MEMORY")
    env.store.incoming("owner-private", "11111", "OWNER_PRIVATE", now=env.now - 500)
    assert await env.workflow.run(env.now)
    assert env.sent[0][0] == "33333"
    assert "OTHER_CONTACT_PRIVATE" not in str(env.calls)
    assert "OTHER_CONTACT_MEMORY" not in str(env.calls)
    assert "OWNER_PRIVATE" not in str(env.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "future",
        "expired",
        "quiet",
        "paused",
        "disabled",
        "blocked",
        "cap_zero",
        "worker",
        "lock",
        "reply_job",
        "unanswered",
    ],
)
async def test_pending_work_sleep_and_restrictions_skip_without_model(env, case):
    task = env.seed(
        due=env.now + 60 if case == "future" else None,
        expires=env.now - 1 if case == "expired" else None,
    )
    now = env.now
    if case == "quiet":
        now = datetime.fromtimestamp(now, TZ).replace(hour=3).timestamp()
    if case == "paused":
        env.cfg["paused"] = True
    if case == "disabled":
        env.cfg["conversation_followups"] = False
    if case in {"blocked", "cap_zero"}:
        env.store.db.execute(
            "INSERT INTO cm_preferences VALUES(?,?,?,?,?)",
            (
                "33333",
                0 if case == "cap_zero" else 2,
                env.now + 600 if case == "blocked" else 0,
                "self restriction",
                0,
            ),
        )
    if case == "worker":
        env.plugin.workers["33333"] = object()
    if case == "lock":
        lock = asyncio.Lock()
        await lock.acquire()
        env.engine.contact_locks["33333"] = lock
    if case == "reply_job":
        env.store.set("reply_job:33333", {"expires": env.now + 600})
    if case == "unanswered":
        env.store.message("33333", "user", "still waiting", now=env.now - 1)
    assert not await env.workflow.run(now)
    assert not env.calls and not env.sent
    if case == "expired":
        assert env.state(task) == "expired"


@pytest.mark.asyncio
async def test_sleep_observation_cancels_due_task(env):
    task = env.seed()
    env.store.incoming("sleep", "33333", "我去睡觉了", now=env.now - 1)
    env.engine.memory.observe("33333", now=env.now - 1)
    assert not await env.workflow.run(env.now)
    assert env.state(task) == "cancelled" and not env.calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["new_incoming", "pause", "revision", "sleep", "expires", "worker"]
)
async def test_context_change_during_generation_cancels_before_send(env, kind):
    task = env.seed()

    async def generate(system, contexts, **options):
        env.calls.append((system, contexts))
        if kind == "new_incoming":
            env.store.incoming("race", "33333", "new message", now=env.now)
        elif kind == "pause":
            env.cfg["paused"] = True
        elif kind == "revision":
            env.cfg["revision"] += 1
        elif kind == "sleep":
            env.store.incoming("sleep-race", "33333", "我先去忙", now=env.now)
            env.engine.memory.observe("33333", now=env.now)
        elif kind == "expires":
            env.store.db.execute(
                "UPDATE cm_followups SET expires=? WHERE id=?", (env.now - 1, task)
            )
        elif kind == "worker":
            env.plugin.workers["33333"] = object()
        return '{"action":"reply","messages":["a specific fact"],"memory":[]}', None

    env.engine.generate = generate
    assert not await env.workflow.run(env.now)
    assert not env.sent
    assert env.state(task) in {"cancelled", "expired"}


@pytest.mark.asyncio
async def test_skipped_and_failed_assessments_are_terminal(env):
    task = env.seed()

    async def skip(*args, **kwargs):
        env.calls.append("skip")
        return '{"action":"skip","messages":[],"memory":[]}', None

    env.engine.generate = skip
    assert not await env.workflow.run(env.now)
    assert env.state(task) == "skipped"
    assert not await env.workflow.run(env.now)
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_unknown_delivery_is_never_retried_and_not_marked_complete(env):
    task = env.seed()

    async def uncertain(contact, text):
        env.sent.append((contact, text))
        raise TimeoutError("receipt unknown")

    env.engine.send = uncertain
    assert not await env.workflow.run(env.now)
    assert env.state(task) == "unknown"
    assert env.store.has_unknown("33333")
    assert not await env.workflow.run(env.now)
    assert len(env.sent) == len(env.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["friend_cap", "personal", "global", "unanswered_ping", "owner_target"]
)
async def test_shared_quotas_and_unanswered_proactive_prevent_bypasses(env, case):
    contact = "11111" if case == "owner_target" else "33333"
    env.seed(contact)
    if case == "owner_target":
        key = f"proactive_target:{env.cfg['bot_qq']}:{datetime.fromtimestamp(env.now, TZ).strftime('%Y-%m-%d')}"
        env.store.set(key, 1)
    kind = (
        "proactive"
        if case == "owner_target"
        else "friend_proactive"
        if case in {"friend_cap", "unanswered_ping"}
        else "reply"
    )
    count = (
        2
        if case == "friend_cap"
        else 20
        if case == "personal"
        else 200
        if case == "global"
        else 1
    )
    for i in range(2 if case == "friend_cap" else 1):
        key = f"quota-{i}"
        env.store.action(
            key, contact, kind, ["x"] * (1 if case == "friend_cap" else count)
        )
        env.store.db.execute(
            "UPDATE actions SET state='succeeded',created=? WHERE id=?",
            (env.now - (1 if case == "unanswered_ping" else 1000), key),
        )
    assert not await env.workflow.run(env.now)
    assert not env.calls and not env.sent


@pytest.mark.asyncio
async def test_one_assessment_per_tick_even_if_several_tasks_due(env):
    env.seed()
    env.seed("11111", "这个问题稍后再查一下再补充给你")
    env.store.set(
        f"proactive_target:{env.cfg['bot_qq']}:{datetime.fromtimestamp(env.now, TZ).strftime('%Y-%m-%d')}",
        5,
    )
    original = env.engine.generate

    async def yielding_generate(*args, **kwargs):
        await asyncio.sleep(0)
        return await original(*args, **kwargs)

    env.engine.generate = yielding_generate
    results = await asyncio.gather(env.workflow.run(env.now), env.workflow.run(env.now))
    assert sum(results) == 1
    assert len(env.calls) == len(env.sent) == 1


@pytest.mark.asyncio
async def test_model_failure_is_terminal_and_only_one_assessment(env):
    task = env.seed()

    async def fail(*args, **kwargs):
        env.calls.append("failed")
        raise ConnectionError("offline")

    env.engine.generate = fail
    assert not await env.workflow.run(env.now)
    assert env.state(task) == "failed"
    assert not await env.workflow.run(env.now)
    assert len(env.calls) == 1


@pytest.mark.asyncio
async def test_stale_source_without_observation_is_cancelled_by_retrieval(env):
    task = env.seed()
    env.store.incoming("changed-topic", "33333", "换个话题", now=env.now - 1)
    env.store.message("33333", "assistant", "好，换个话题", now=env.now)
    assert not await env.workflow.run(env.now)
    assert env.state(task) == "cancelled"
    assert not env.calls


@pytest.mark.asyncio
async def test_forget_generation_prevents_task_id_reuse_from_blocking_new_work(env):
    first = env.seed()
    original = env.engine.generate

    async def skip(*args, **kwargs):
        return '{"action":"skip","messages":[],"memory":[]}', None

    env.engine.generate = skip
    assert not await env.workflow.run(env.now)
    assert env.state(first) == "skipped"
    env.engine.memory.reset("33333", forget_history=True)
    second = env.seed()
    assert second == first
    env.engine.generate = original
    assert await env.workflow.run(env.now)
    assert env.state(second) == "completed"


@pytest.mark.asyncio
async def test_forget_during_model_does_not_send_or_cancel_new_same_id_task(env):
    first = env.seed()
    replacement = []

    async def forgotten(*args, **kwargs):
        env.engine.memory.reset("33333", forget_history=True)
        replacement.append(env.seed())
        return (
            '{"action":"reply","messages":["old forgotten content"],"memory":[]}',
            None,
        )

    env.engine.generate = forgotten
    assert not await env.workflow.run(env.now)
    assert replacement == [first]
    assert env.state(first) == "pending"
    assert not env.sent
