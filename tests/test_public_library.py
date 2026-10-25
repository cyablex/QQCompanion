# ruff: noqa: E402
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from digital_activity import DigitalActivities
from engine import Engine
from public_library import PublicLibrary
from store import Store, TZ


def record(ident, title="旋转茶杯", **extra):
    return dict(id=ident, day=ident, kind="reading_note", title=title, content="实际读到的公开材料：旋转茶杯的图案形成了一种有趣的视觉效果。", next_step="还想了解这个图案为什么看起来在旋转。", source=dict(url="https://www.dpm.org.cn/collection/ceramic/123.html", category="art", scope="page_excerpt"), origin="independent_public_activity", public_safe=True, status="completed", **extra)


@pytest.fixture
def env(tmp_path):
    store = Store(tmp_path / "library.sqlite3")
    cfg = dict(owner_qq="11111", bot_qq="22222", name="大肥鱼", persona="自然", owner_title="主人", revision=1, live=True, paused=False, guests=True, digital_activities=True, daily_calls=1000, daily_cny=3, monthly_cny=60)
    prompts = []

    async def generate(system, contexts, **kwargs):
        prompts.append((system, contexts))
        return "记得那篇文章里的细节。", None

    engine = Engine(store, generate, None, lambda: dict(cfg))
    engine.activities = DigitalActivities(store, tmp_path)
    store.set("known_friend_ids", ["33333"])
    yield cfg, store, engine, prompts
    store.db.close()


@pytest.mark.asyncio
async def test_old_reading_survives_recent_window_and_reaches_real_chat_prompt(env):
    cfg, store, engine, prompts = env
    start = datetime(2026, 1, 1)
    for i in range(45):
        day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        row = record(day, "旋转茶杯" if i == 0 else "星空随笔" + str(i))
        if i:
            row.update(content="本次真实保存的是另一份星空阅读笔记，没有涉及那个旧话题。", next_step="观察新的星空知识。")
        engine.activities._save(cfg, row)
    assert len(engine.activities.records(cfg)) == 40
    store.incoming("question", "11111", "还记得以前那篇旋转茶杯吗？")
    assert await engine.decision("11111", "reply", "回复")
    assert "2026-01-01" in prompts[-1][0] and "旋转茶杯的图案" in prompts[-1][0]
    restarted = DigitalActivities(store, engine.activities.root)
    assert "旋转茶杯的图案" in restarted.context("33333", cfg, query="旋转茶杯")
    assert restarted.context("44444", cfg, query="旋转茶杯") == ""
    assert "旋转茶杯" not in restarted.context("11111", dict(cfg, bot_qq="99999"), query="旋转茶杯")


def test_failed_private_or_unsafe_records_never_enter_library(env):
    cfg, store, engine, _ = env
    library = engine.activities.library
    for i, change in enumerate([dict(status="failed"), dict(origin="friend_chat"), dict(public_safe=False)]):
        row = record(str(i))
        row.update(change)
        library.save(cfg, row)
    assert library.records(cfg) == library.interests(cfg) == []
    store.remember("11111", "PRIVATE SECRET")
    library.save(cfg, record("2026-01-01"))
    assert "PRIVATE" not in str(library.records(cfg))


def test_interest_continuation_requires_actual_answer_to_mark_resolved(env):
    cfg, _, engine, _ = env
    worker = engine.activities
    worker._save(cfg, record("2026-01-01", interest_topic="图案与视觉", open_question="图案为什么看起来会旋转？"))
    interest = worker.library.interests(cfg)[0]
    invalid = record("2026-01-02", interest_id=interest["id"], interest_status="resolved", resolution="只是计划明天查")
    worker._save(cfg, invalid)
    assert worker.library.interest(cfg, interest["id"])["status"] == "active"
    answered = record("2026-01-03", interest_id=interest["id"], interest_status="resolved", resolution="这次笔记已经给出了该视觉效应的解释。")
    answered["content"] += answered["resolution"]
    worker._save(cfg, answered)
    assert worker.library.interest(cfg, interest["id"])["status"] == "resolved"
    assert all(row["id"] != interest["id"] for row in worker.library.interests(cfg))
    assert PublicLibrary(worker.store).interest(cfg, interest["id"])["status"] == "resolved"


@pytest.mark.asyncio
async def test_planner_gets_real_interests_and_can_pause_without_pretending_progress(env):
    cfg, store, engine, _ = env
    worker = engine.activities
    worker._save(cfg, record("2026-01-01", interest_topic="器物图案", open_question="为什么画这种纹样？"))
    interest = worker.library.interests(cfg)[0]
    calls = []

    async def auxiliary(kind, system, contexts, **kwargs):
        payload = json.loads(contexts[0]["content"])
        calls.append(payload)
        assert payload["interest_lines"][0]["id"] == interest["id"]
        assert payload["actual_category_choices"] == {"art": 1}
        return json.dumps(dict(action="skip", pause_interest_id=interest["id"]))

    engine.generate_aux = auxiliary
    row = await worker.run(engine, cfg, now=datetime(2026, 10, 22, 12, tzinfo=TZ))
    assert row["status"] == "skipped" and len(calls) == 1
    assert worker.library.interest(cfg, interest["id"])["status"] == "paused"
    assert len(worker.library.records(cfg)) == 1


@pytest.mark.asyncio
async def test_foreign_interest_id_cannot_be_used_or_changed(env):
    cfg, store, engine, _ = env
    foreign = dict(cfg, bot_qq="99999")
    library = engine.activities.library
    library.save(foreign, record("2026-01-01", interest_topic="FOREIGN TOPIC"))
    ident = library.interests(foreign)[0]["id"]

    async def auxiliary(kind, system, contexts, **kwargs):
        assert "FOREIGN" not in contexts[0]["content"]
        return json.dumps(dict(action="read", category="art", query="器物", interest_id=ident))

    engine.generate_aux = auxiliary
    result = await engine.activities.run(engine, cfg, now=datetime(2026, 10, 22, 12, tzinfo=TZ))
    assert result["status"] == "failed"
    library.pause(cfg, ident)
    assert library.interest(foreign, ident)["status"] == "active"


def test_existing_public_records_are_imported_once_with_their_real_source(env):
    cfg, store, engine, _ = env
    worker = engine.activities
    original = record("2026-01-01")
    store.set(worker._key(cfg), [original])
    assert worker.public_records(cfg)[0]["source"]["url"] == original["source"]["url"]
    interest = worker.library.interests(cfg)[0]
    worker.library.pause(cfg, interest["id"])
    assert len(worker.public_records(cfg)) == 1
    assert worker.library.interest(cfg, interest["id"])["status"] == "paused"
