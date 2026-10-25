import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from budget_breakdown import read_budget  # noqa: E402
from model_billing import TZ  # noqa: E402
from relay_billing import RelayBilling  # noqa: E402
from store import Store, BudgetExceeded  # noqa: E402


@pytest.fixture
def billing(tmp_path):
    store = Store(tmp_path / "billing.sqlite3")
    day = datetime.now(TZ).strftime("%Y-%m-%d")
    cfg = dict(model_route="relay", relay_billing_sync=True, relay_billing_since=day,
               relay_credit_cny=1, daily_calls=100, daily_cny=1, monthly_cny=1)
    yield store, cfg, day
    store.db.close()


def complete(store, cfg, kind, amount):
    rid = store.reserve(cfg, kind, amount)
    store.settle(rid, amount)
    return rid


def response(day, amount):
    return dict(unit="USD", daily_usage=[dict(date=day, actual_cost=amount, requests=2)]), dict(peak_rate_enabled=False)


@pytest.mark.asyncio
async def test_real_bill_replaces_relay_estimate_and_new_local_calls_are_not_lost(billing):
    store, cfg, day = billing
    complete(store, cfg, "reply", .5)
    complete(store, cfg, "web_fetch", .005)  # official tool must not be subtracted
    monitor = RelayBilling(store, lambda _: response(day, .4))
    assert await monitor.sync(cfg)
    budget = read_budget(store.db, day)
    assert budget["usage_estimate"] == .405
    assert budget["billing_sync"]["relay_today"] == .4
    assert budget["billing_sync"]["adjustment"] == -.1
    assert sum(row["usage_estimate"] for row in budget["breakdown"]) == pytest.approx(.405)
    complete(store, cfg, "reply", .1)
    assert read_budget(store.db, day)["usage_estimate"] == .505
    monitor.next_sync = 0
    monitor.request = lambda _: response(day, .5)
    assert await monitor.sync(cfg)
    assert read_budget(store.db, day)["usage_estimate"] == .505


@pytest.mark.asyncio
async def test_failed_call_releases_daily_and_monthly_limits_and_billed_failure_is_reconciled(billing):
    store, cfg, day = billing
    complete(store, cfg, "reply", .4)
    rid = store.reserve(cfg, "reply", .5)
    store.settle(rid, state="failed_or_unknown")
    budget = read_budget(store.db, day)
    assert budget["today"] == .4 and budget["month"] == .4
    assert budget["unconfirmed"] == .5 and budget["active_reserve"] == 0
    monitor = RelayBilling(store, lambda _: response(day, .45))
    assert await monitor.sync(cfg)
    assert read_budget(store.db, day)["usage_estimate"] == .45  # not assumed free
    rid = store.reserve(cfg, "reply", .5)
    with pytest.raises(BudgetExceeded):
        store.reserve(cfg, "reply", .1)
    store.settle(rid, state="estimated")
    assert read_budget(store.db, day)["today"] == .45


@pytest.mark.asyncio
async def test_billing_read_failure_preserves_previous_bill_and_new_usage(billing):
    store, cfg, day = billing
    complete(store, cfg, "reply", .5)
    monitor = RelayBilling(store, lambda _: response(day, .4))
    assert await monitor.sync(cfg)
    complete(store, cfg, "reply", .1)
    monitor.next_sync = 0

    def offline(_):
        raise TimeoutError("private response must not be exposed")

    monitor.request = offline
    assert not await monitor.sync(cfg)
    budget = read_budget(store.db, day)
    assert budget["usage_estimate"] == .5 and budget["billing_sync"]["error"] == "TimeoutError"


@pytest.mark.asyncio
async def test_sync_is_skipped_if_a_new_call_occurs_during_http(billing):
    store, cfg, day = billing
    complete(store, cfg, "reply", .4)
    started, finish = asyncio.Event(), asyncio.Event()

    async def to_thread(request, cfg):
        started.set()
        await finish.wait()
        return response(day, .3)

    from unittest.mock import patch
    with patch("relay_billing.asyncio.to_thread", to_thread):
        task = asyncio.create_task(RelayBilling(store).sync(cfg))
        await started.wait()
        complete(store, cfg, "reply", .1)
        finish.set()
        assert not await task
    assert read_budget(store.db, day)["usage_estimate"] == .5


@pytest.mark.asyncio
async def test_currency_conversion_and_validation_exemption(billing):
    store, cfg, day = billing
    cfg["relay_credit_cny"] = 2
    complete(store, cfg, "reply", .3)
    complete(store, cfg, "validation_probe", .1)
    monitor = RelayBilling(store, lambda _: response(day, .15))
    assert await monitor.sync(cfg)
    budget = read_budget(store.db, day)
    assert budget["today"] == .3 and budget["validation"] == .1
    assert budget["month"] == .3  # platform total .15 USD * 2 CNY


@pytest.mark.asyncio
async def test_bad_currency_does_not_replace_a_valid_bill(billing):
    store, cfg, day = billing
    complete(store, cfg, "reply", .4)
    payload, rates = response(day, .1)
    payload["unit"] = "unknown"
    assert not await RelayBilling(store, lambda _: (payload, rates)).sync(cfg)
    assert read_budget(store.db, day)["usage_estimate"] == .4
