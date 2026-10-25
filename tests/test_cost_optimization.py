import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import conversation_memory
from conversation_memory import ConversationMemory
from engine import Engine
from store import BudgetExceeded, Store, TZ


def test_validation_budget_is_separate_and_both_keep_monthly_total(tmp_path):
    store = Store(tmp_path / "budget.sqlite3")
    cfg = dict(daily_calls=1, daily_cny=0.2, monthly_cny=1,
               validation_daily_calls=1, validation_daily_cny=0.3)
    rid = store.reserve(cfg, "reply", 0.2)
    store.settle(rid, 0.1)
    test_id = store.reserve(cfg, "validation_voice", 0.3)
    store.settle(test_id, 0.25)
    with pytest.raises(BudgetExceeded):
        store.reserve(cfg, "acceptance_model", 0.01)
    with pytest.raises(BudgetExceeded):
        store.reserve(cfg, "reply", 0.01)
    cfg["daily_calls"] = 2
    store.reserve(cfg, "reply", 0.05)
    status = store.status()
    assert status["calls_today"] == 2 and status["validation_calls_today"] == 1
    assert status["estimated_cny_today"] == 0.1 and status["validation_cny_today"] == 0.25
    assert status["active_cny_today"] == .05 and status["limit_cny_today"] == .15
    assert status["all_estimated_cny_today"] == 0.35
    cfg.update(daily_calls=10, daily_cny=2)
    with pytest.raises(BudgetExceeded):
        store.reserve(cfg, "reply", 0.7)
    store.db.close()


def test_known_usage_is_not_overwritten_as_unknown_after_parse_failure(tmp_path):
    store = Store(tmp_path / "usage.sqlite3")
    cfg = dict(daily_calls=10, daily_cny=3, monthly_cny=60)
    rid = store.reserve(cfg, "qzone_review", 0.14)
    store.settle(rid, 0.003)
    store.settle(rid, state="failed_or_unknown")
    row = store.db.execute("SELECT state,amount FROM calls WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "completed_with_error" and row["amount"] == 0.003
    uncertain = store.reserve(cfg, "qzone_review", 0.14)
    store.settle(uncertain, state="failed_or_unknown")
    row = store.db.execute("SELECT state,amount FROM calls WHERE id=?", (uncertain,)).fetchone()
    assert row["state"] == "failed_or_unknown" and row["amount"] == 0.14
    store.db.close()


@pytest.mark.asyncio
async def test_memory_batches_small_new_exchange_until_idle_and_keeps_raw_history(tmp_path, monkeypatch):
    store = Store(tmp_path / "memory.sqlite3")
    memory = ConversationMemory(store)
    clock = [time.time()]
    monkeypatch.setattr(conversation_memory.time, "time", lambda: clock[0])
    calls = []

    class Extract:
        async def generate_aux(self, kind, system, contexts, **kwargs):
            calls.append(contexts)
            return json.dumps(dict(facts=[], episodes=[], diary=[], followups=[]))

    store.message("11111", "user", "今天看了一本小书")
    store.message("11111", "assistant", "哪本呀")
    assert await memory.consolidate("11111", Extract(), {})
    clock[0] += 700
    store.message("11111", "user", "是朋友推荐的那本")
    store.message("11111", "assistant", "原来如此")
    assert not await memory.consolidate("11111", Extract(), {})
    assert "朋友推荐" in store.history("11111")[-2]["content"]
    assert len(calls) == 1
    clock[0] += 901
    assert await memory.consolidate("11111", Extract(), {})
    assert len(calls) == 2
    store.db.close()


@pytest.mark.asyncio
async def test_explicit_memory_correction_does_not_wait_for_full_batch(tmp_path, monkeypatch):
    store = Store(tmp_path / "correction.sqlite3")
    memory = ConversationMemory(store)
    now = [time.time()]
    monkeypatch.setattr(conversation_memory.time, "time", lambda: now[0])

    class Extract:
        async def generate_aux(self, *args, **kwargs):
            return '{"facts":[],"episodes":[],"diary":[],"followups":[]}'

    store.message("11111", "user", "今天有点忙")
    store.message("11111", "assistant", "好呀")
    assert await memory.consolidate("11111", Extract(), {})
    now[0] += 121
    store.message("11111", "user", "更正一下，以后叫我小陈")
    assert await memory.consolidate("11111", Extract(), {})
    store.db.close()


@pytest.mark.asyncio
async def test_owner_skip_waits_two_to_four_hours_without_repeated_model_calls(tmp_path):
    store = Store(tmp_path / "outreach.sqlite3")
    now = datetime.now(TZ).replace(hour=12)
    cfg = dict(owner_qq="11111", bot_qq="22222", proactive=True, live=True, paused=False,
               daily_chat_messages=500, proactive_min=3, proactive_max=6)
    calls = []
    engine = Engine(store, None, None, lambda: dict(cfg))
    store.incoming("old", "11111", "闲聊", now.timestamp() - 5 * 3600)

    async def decision(*args, **kwargs):
        calls.append(args)
        return dict(action="skip", messages=[])

    engine.decision = decision
    assert not await engine.proactive(now)
    until = store.get("owner_proactive_next")
    assert now.timestamp() + 2 * 3600 <= until <= now.timestamp() + 4 * 3600
    assert not await engine.proactive(now)
    assert len(calls) == 1

    async def reply(*args, **kwargs):
        return dict(action="reply", messages=["有一件具体的小事想说"])

    async def delivered(*args, **kwargs):
        return True

    engine.decision = reply
    engine.deliver = delivered
    later = now + timedelta(hours=4)
    assert await engine.proactive(later)
    assert store.get("owner_proactive_next") == later.timestamp() + 90 * 60
    store.db.close()
