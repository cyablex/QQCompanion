"""Verify price boundaries, captured usage and honest display of unknown costs."""

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "astrbot"))

from budget_breakdown import period_totals, summarize  # noqa: E402
from engine import Engine  # noqa: E402
from model_billing import TZ, billing_snapshot, model_rates, usage_cost  # noqa: E402
from store import BudgetExceeded, Store  # noqa: E402


@pytest.mark.parametrize("stamp,period", [
    ("2026-10-23T08:59:59+08:00", "offpeak"),
    ("2026-10-23T09:00:00+08:00", "peak"),
    ("2026-10-23T11:59:59+08:00", "peak"),
    ("2026-10-23T12:00:00+08:00", "offpeak"),
    ("2026-10-23T13:59:59+08:00", "offpeak"),
    ("2026-10-23T14:00:00+08:00", "peak"),
    ("2026-10-23T17:59:59+08:00", "peak"),
    ("2026-10-23T18:00:00+08:00", "offpeak"),
    ("2026-10-24T10:00:00+08:00", "offpeak"),
    ("2026-10-25T15:00:00+08:00", "offpeak"),
    ("2026-10-01T10:00:00+08:00", "offpeak"),
    ("2026-02-23T15:00:00+08:00", "offpeak"),
    ("2026-10-10T10:00:00+08:00", "offpeak"),  # weekend makeup workday
])
def test_official_boundaries_holidays_and_weekends(stamp, period):
    cfg = {"model_route": "official", "model": "deepseek-flash"}
    snap = billing_snapshot(cfg, datetime.fromisoformat(stamp))
    assert snap["billing_context"]["period"] == period
    assert snap["billing_context"]["calendar_verified"]
    assert snap["billing_rates"]["output"] == (8 if period == "peak" else 4)
    assert model_rates(cfg)["output"] == 8  # conservative reservation unchanged


def test_timezones_and_unknown_calendar_year():
    cfg = {"model_route": "official"}
    moment = datetime(2026, 10, 23, 1, tzinfo=timezone.utc)
    snap = billing_snapshot(cfg, moment)
    assert snap["billing_context"]["period"] == "peak"
    assert snap["billing_context"]["request_started"].endswith("+08:00")
    future = billing_snapshot(cfg, datetime(2027, 1, 1, 10, tzinfo=TZ))
    assert not future["billing_context"]["calendar_verified"]
    assert future["billing_rates"]["output"] == 8  # no invented holiday calendar


def test_relay_does_not_assume_the_official_offpeak_discount():
    cfg = dict(model_route="relay", relay_connection=dict(model="deepseek-v4.1-flash",
               pricing_cny=dict(input_other=.6, input_cached=.012, output=2.4)))
    snapshots = [billing_snapshot(cfg, datetime(2026, 10, 23, hour, tzinfo=TZ)) for hour in (1, 10)]
    assert snapshots[0]["billing_rates"] == snapshots[1]["billing_rates"]
    assert snapshots[0]["billing_context"]["period"] == "fixed_unverified_schedule"
    usage = dict(input_other=1000, input_cached=1000, output=1000, **snapshots[0])
    assert usage_cost(usage, cfg) == pytest.approx(.003012)


@pytest.mark.asyncio
async def test_adapter_captures_actual_route_and_price_before_generation(monkeypatch):
    os.environ["QQCOMPANION_ROOT"] = str(ROOT)
    spec = importlib.util.spec_from_file_location("billing_adapter_test", ROOT / "plugins/astrbot_plugin_qq_companion/main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = dict(model_route="relay", model="deepseek-flash", relay_connection=dict(
        model="deepseek-v4.1-flash", pricing_cny=dict(input_other=.6, input_cached=.012, output=2.4)))
    monkeypatch.setattr(module, "read_settings", lambda: cfg)
    moment = [datetime(2026, 10, 23, 8, 59, tzinfo=TZ)]
    monkeypatch.setattr(module, "billing_snapshot", lambda active: billing_snapshot(active, moment[0]))
    calls = []

    async def generate(**kwargs):
        calls.append(kwargs["chat_provider_id"])
        moment[0] = datetime(2026, 10, 23, 9, 1, tzinfo=TZ)
        return SimpleNamespace(completion_text="ok", usage=SimpleNamespace(
            input_other=1000, input_cached=1000, output=1000, total=3000),
            raw_completion=SimpleNamespace(id="request-test-only"))

    plugin = module.QQCompanion.__new__(module.QQCompanion)
    plugin.context = SimpleNamespace(llm_generate=generate)
    _, usage = await plugin.generate("s", [], response_mode="text", official_fallback=True)
    assert calls == ["deepseek_official_fallback_text"]
    assert usage["billing_context"]["route"] == "official"
    assert usage["billing_context"]["period"] == "offpeak"
    assert usage["billing_context"]["request_id"] == "request-test-only"
    assert usage_cost(usage, cfg) == pytest.approx(.00502)


@pytest.mark.asyncio
async def test_settlement_saves_usage_even_when_no_qq_message_is_sent(tmp_path):
    store = Store(tmp_path / "billing.sqlite3")
    cfg = dict(live=True, paused=False, revision=1, daily_calls=100, daily_cny=3, monthly_cny=60)
    snap = billing_snapshot(cfg, datetime(2026, 10, 23, 1, tzinfo=TZ))
    usage = dict(input_other=1000, input_cached=2000, output=300, **snap)

    async def generate(*args, **kwargs):
        return "generated but unused", usage

    engine = Engine(store, generate, None, lambda: cfg)
    assert await engine.generate_aux("public_novelty", "s", []) == "generated but unused"
    row = store.db.execute("SELECT c.amount,c.state,b.details FROM calls c JOIN call_billing b USING(id)").fetchone()
    assert row["amount"] == pytest.approx(.00224) and row["state"] == "completed"
    assert json.loads(row["details"])["input_cached"] == 2000
    assert not store.db.execute("SELECT 1 FROM deliveries").fetchone()
    rid = store.db.execute("SELECT id FROM calls").fetchone()[0]
    store.settle(rid, state="failed_or_unknown")
    assert store.db.execute("SELECT state FROM calls").fetchone()[0] == "completed_with_error"
    store.db.close()


def test_unknown_releases_limit_while_live_reservation_still_protects_it(tmp_path):
    store = Store(tmp_path / "unknown.sqlite3")
    cfg = dict(daily_calls=100, daily_cny=1, monthly_cny=60)
    rid = store.reserve(cfg, "reply", .4)
    store.settle(rid, .4)
    for state in ("estimated", "failed_or_unknown", "interrupted", "reserved"):
        rid = store.reserve(cfg, "reply", .1)
        store.settle(rid, state=state)
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    rows = store.db.execute("SELECT day,kind,state,SUM(amount),COUNT(*) FROM calls GROUP BY day,kind,state").fetchall()
    budget = summarize(rows, day)
    assert budget["usage_estimate"] == .4
    assert budget["unconfirmed"] == .3 and budget["today"] == .5
    assert budget["active_reserve"] == .1
    assert budget["breakdown"][0]["usage_estimate"] == .4
    store.reserve(cfg, "reply", .3)
    with pytest.raises(BudgetExceeded):
        store.reserve(cfg, "reply", .3)
    store.db.close()


def test_period_totals_do_not_guess_old_records_or_mix_in_unknown_fees():
    official = billing_snapshot({}, datetime(2026, 10, 23, 1, tzinfo=TZ))
    periods = period_totals([(.1, json.dumps(official))], .4)
    assert periods == [dict(id="official_offpeak", label="官方 · 空闲", amount=.1),
                       dict(id="unclassified", label="旧记录或按次工具 · 未分时段", amount=.3)]
