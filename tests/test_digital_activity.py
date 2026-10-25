import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import digital_activity as activities
from digital_activity import DigitalActivities
from reading_sources import BY_ID
from store import Store, TZ


NOW = datetime(2026, 10, 20, 14, 0, tzinfo=TZ)


class FakeEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calls = []
        self.on_generate = None
        self.plans = []

    def config_reader(self):
        return dict(self.cfg)

    async def generate_aux(self, kind, system, contexts, **options):
        self.calls.append((kind, system, contexts, options))
        if kind == 'digital_activity_plan':
            return json.dumps(self.plans.pop(0) if self.plans else dict(action='read', category='nature', query='有趣的植物'))
        if self.on_generate:
            self.on_generate()
        return json.dumps(
            {
                "action": "complete",
                "title": "鲸鱼的歌",
                "content": "这是一段实际生成并保存的海洋阅读笔记或原创短篇。今天只读了所给节选，明天想写成一段小故事。",
                "next_step": "把歌声改成故事开头",
            },
            ensure_ascii=False,
        )


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = Store(tmp_path / "facts.sqlite3")
    cfg = dict(
        live=True,
        paused=False,
        bot_qq="22222",
        owner_qq="11111",
        digital_activities=True,
        revision=1,
    )
    engine = FakeEngine(cfg)
    worker = DigitalActivities(store, tmp_path)
    store.set("known_friend_ids", ["33333"])

    async def source(engine, cfg, category, query, seen):
        return dict(
            title='公开材料', url='https://public.example/' + category, category=category,
            text="PUBLIC_SOURCE_EXCERPT " * 50,
            scope="page_excerpt",
            retrieved_at=NOW.timestamp(),
            truncated=True,
            sha256="source-sha",
        )

    monkeypatch.setattr(activities, "discover_article", source)
    yield store, cfg, engine, worker
    store.db.close()


@pytest.mark.asyncio
async def test_real_artifacts_and_private_sources_never_read(env, monkeypatch):
    store, cfg, engine, worker = env
    store.incoming("private", "44444", "OTHER_FRIEND_SECRET")
    store.remember("11111", "OWNER_SECRET")

    def forbidden(*args):
        raise AssertionError("Digital activity accessed private conversation")

    monkeypatch.setattr(store, "history", forbidden)
    monkeypatch.setattr(store, "memories", forbidden)
    result = await worker.run(engine, cfg, now=NOW)
    assert result["status"] == "completed"
    assert result["source"]["scope"] == "page_excerpt"
    assert result["requested_source"]["category"] in BY_ID
    path = Path(result["artifact"])
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["content"] == result["content"]
    assert result["content"] in path.with_suffix(".md").read_text(encoding="utf-8")
    assert "PUBLIC_SOURCE_EXCERPT" in saved["read_excerpt"]
    assert "OTHER_FRIEND_SECRET" not in str(engine.calls)
    assert "OWNER_SECRET" not in str(engine.calls)
    for contact in ("11111", "33333"):
        context = worker.context(contact, cfg)
        assert result["content"] in context
        assert "PRIVATE" not in context
    assert worker.context("44444", cfg) == ""
    assert worker.context("group:33333", cfg) == ""
    assert result["content"] in worker.context("anywhere", cfg, public=True)


@pytest.mark.asyncio
async def test_daily_cap_including_force_and_restart(env):
    store, cfg, engine, worker = env
    assert await worker.run(engine, cfg, now=NOW)
    assert await worker.run(engine, cfg, now=NOW, force=True) is None
    restarted = DigitalActivities(store, worker.root)
    assert await restarted.run(engine, cfg, now=NOW, force=True) is None
    assert len(engine.calls) == 2


@pytest.mark.asyncio
async def test_cross_day_continuation_uses_actual_saved_work(env):
    _, cfg, engine, worker = env
    engine.plans = [dict(action="read", category="nature", query="种子"), dict(action="creative_draft", parent_id=NOW.strftime("%Y-%m-%d")), dict(action="creative_revision", parent_id=(NOW + timedelta(days=1)).strftime("%Y-%m-%d")), dict(action="read", category="everyday", query="声音")]
    first = await worker.run(engine, cfg, now=NOW)
    second = await worker.run(engine, cfg, now=NOW + timedelta(days=1))
    assert first["kind"] == "reading_note"
    assert second["kind"] == "creative_draft"
    assert second["parent_id"] == first["id"]
    prompt = json.loads(engine.calls[-1][2][0]["content"])
    assert prompt["material"]["previous_content"] == first["content"]
    third = await worker.run(engine, cfg, now=NOW + timedelta(days=2))
    assert third["kind"] == "creative_revision"
    assert third["version"] == 2 and third["parent_id"] == second["id"]
    fourth = await worker.run(engine, cfg, now=NOW + timedelta(days=3))
    assert fourth["kind"] == "reading_note"
    assert fourth["source"]["url"] != first["source"]["url"]


@pytest.mark.asyncio
async def test_failed_fetch_is_not_a_reading_and_never_uses_model(env, monkeypatch):
    _, cfg, engine, worker = env

    async def fail(*args):
        raise TimeoutError("arbitrary remote body must not be recorded")

    monkeypatch.setattr(activities, "discover_article", fail)
    result = await worker.run(engine, cfg, now=NOW)
    assert result["status"] == "source_unavailable"
    assert "source" not in result and not result["content"]
    assert result["requested_source"]["category"] in BY_ID
    assert len(engine.calls) == 1 and engine.calls[0][0] == "digital_activity_plan"
    assert worker.context("11111", cfg) == ""
    assert await worker.run(engine, cfg, now=NOW, force=True) is None
    assert "arbitrary remote" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["fetch", "generation"])
async def test_pause_race_discards_results_before_commit(env, monkeypatch, stage):
    _, cfg, engine, worker = env
    original = activities.discover_article
    if stage == "fetch":

        async def pause_source(*args):
            engine.cfg["paused"] = True
            return await original(*args)

        monkeypatch.setattr(activities, "discover_article", pause_source)
    else:
        engine.on_generate = lambda: engine.cfg.update(paused=True)
    result = await worker.run(engine, dict(cfg), now=NOW)
    assert result is None
    assert worker.records(cfg) == []
    assert not list(worker.root.glob("data/activities/**/*.md"))
    assert len(engine.calls) == (1 if stage == "fetch" else 2)


@pytest.mark.asyncio
async def test_revision_change_discards_generation_and_does_not_retry(env):
    _, cfg, engine, worker = env
    engine.on_generate = lambda: engine.cfg.update(revision=2)
    assert await worker.run(engine, dict(cfg), now=NOW) is None
    assert worker.records(cfg) == []
    assert await worker.run(engine, cfg, now=NOW) is None


@pytest.mark.asyncio
async def test_quiet_disabled_and_stale_run_have_no_side_effects(env):
    _, cfg, engine, worker = env
    assert await worker.run(engine, cfg, now=NOW.replace(hour=7), force=True) is None
    assert await worker.run(engine, dict(cfg, paused=True), now=NOW) is None
    assert await worker.run(engine, dict(cfg, live=False), now=NOW) is None
    assert (
        await worker.run(engine, dict(cfg, digital_activities=False), now=NOW) is None
    )
    assert await worker.run(engine, dict(cfg, revision=9), now=NOW) is None
    assert worker.records(cfg) == [] and engine.calls == []
    assert await worker.run(engine, cfg, now=NOW)


@pytest.mark.asyncio
async def test_concurrent_runs_only_assess_once(env):
    _, cfg, engine, worker = env
    results = await asyncio.gather(
        *(worker.run(engine, cfg, now=NOW) for _ in range(6))
    )
    assert sum(result is not None for result in results) == 1
    assert len(engine.calls) == 2


@pytest.mark.asyncio
async def test_invalid_or_skipped_model_output_never_claims_finished_activity(env):
    _, cfg, engine, worker = env

    async def invalid(*args, **kwargs):
        return '{"action":"complete","title":"完成","content":""}'

    engine.generate_aux = invalid
    result = await worker.run(engine, cfg, now=NOW)
    assert result["status"] == "failed"
    assert worker.context("11111", cfg) == ""

    async def skipped(*args, **kwargs):
        return '{"action":"skip"}'

    engine.generate_aux = skipped
    result = await worker.run(engine, cfg, now=NOW + timedelta(days=1))
    assert result["status"] == "skipped"
    assert worker.context("11111", cfg) == ""


@pytest.mark.asyncio
async def test_context_filters_private_rows_failures_and_other_bot(env):
    store, cfg, engine, worker = env
    good = await worker.run(engine, cfg, now=NOW)
    rows = worker.records(cfg)
    rows.extend(
        [
            dict(good, content="PRIVATE_ROW", origin="friend_chat"),
            dict(good, content="FAILED_ROW", status="failed"),
            dict(good, content="UNSAFE_ROW", public_safe=False),
        ]
    )
    store.set(worker._key(cfg), rows)
    for public in (False, True):
        context = worker.context("11111", cfg, public=public)
        assert (
            "PRIVATE_ROW" not in context
            and "FAILED_ROW" not in context
            and "UNSAFE_ROW" not in context
        )
    assert worker.context("11111", dict(cfg, bot_qq="99999")) == ""


