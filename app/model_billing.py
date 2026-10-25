"""Use the active model's verified CNY rates for reserves and settlement."""

import math
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
OFFICIAL_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
# 国办发明电〔2025〕7号；周末（包括调休上班日）按供应商说明仍为空闲。
HOLIDAYS = {2026: (("01-01", "01-03"), ("02-15", "02-23"), ("04-04", "04-06"),
                   ("05-01", "05-05"), ("06-19", "06-21"), ("09-25", "09-27"), ("10-01", "10-07"))}


def billing_snapshot(cfg, now=None):
    """Freeze request-start pricing; reservations deliberately keep peak rates."""
    moment = datetime.now(TZ) if now is None else now.astimezone(TZ)
    route = cfg.get("model_route", "official")
    rates = model_rates(cfg)
    context = dict(route=route, request_started=moment.isoformat(), pricing_version="2026-10-23")
    if route == "official":
        date = moment.strftime("%m-%d")
        holiday = any(start <= date <= end for start, end in HOLIDAYS.get(moment.year, ()))
        minute = moment.hour * 60 + moment.minute
        peak = moment.weekday() < 5 and not holiday and (540 <= minute < 720 or 840 <= minute < 1080)
        context.update(model=cfg.get("model", "deepseek-flash"), period="peak" if peak else "offpeak",
                       calendar_verified=moment.year in HOLIDAYS, source=OFFICIAL_SOURCE)
        if not peak:
            rates = {key: value / 2 for key, value in rates.items()}
    else:
        relay = cfg.get("relay_connection", {})
        context.update(model=relay.get("model", ""), period="fixed_unverified_schedule",
                       source="已核实的中转配置单价；峰谷规则未核实")
    return dict(billing_rates=rates, billing_context=context)


def model_rates(cfg):
    if cfg.get("model_route", "official") == "official":
        return dict(input_other=2.0, input_cached=0.04, output=8.0)
    rates = cfg.get("relay_connection", {}).get("pricing_cny", {})
    if any(
        not isinstance(rates.get(k), (int, float))
        or not math.isfinite(rates[k])
        or rates[k] < 0
        for k in ("input_other", "input_cached", "output")
    ):
        raise ValueError("Verified relay CNY prices are required")
    return {k: rates[k] for k in ("input_other", "input_cached", "output")}


def usage_cost(usage, cfg):
    rates = usage.get("billing_rates") or model_rates(cfg)
    return (
        sum(
            usage.get(k, 0) * rates[k]
            for k in ("input_other", "input_cached", "output")
        )
        / 1_000_000
    )
