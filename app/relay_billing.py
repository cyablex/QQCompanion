"""Read this relay key's billed usage and reconcile aggregates, never send prompts."""

import asyncio
import json
import math
import time
import urllib.request
from datetime import datetime
from urllib.parse import urlsplit

from settings import load_key
from model_billing import TZ


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_usage(cfg):
    parts = urlsplit(cfg["relay_connection"]["base_url"])
    if parts.scheme != "https" or parts.username or parts.password:
        raise ValueError("An HTTPS relay origin is required")
    origin = parts.scheme + "://" + parts.netloc
    key = load_key(relay=True)
    opener = urllib.request.build_opener(NoRedirect)

    def get(endpoint):
        request = urllib.request.Request(origin + endpoint, headers={
            "Authorization": "Bearer " + key, "Accept": "application/json",
            "User-Agent": "QQCompanion-billing-readonly/4.22",
        })
        with opener.open(request, timeout=8) as response:
            return json.load(response)

    billing = get("/v1/sub2api/billing")
    if billing.get("billing_scope") != "token":
        raise ValueError("Billing scope is not this API key")
    usage = get("/v1/usage?timezone=Asia%2FShanghai")
    return usage, billing


def relay_estimates(db, since):
    result = {}
    rows = db.execute(
        "SELECT c.day,c.kind,c.amount,b.details FROM calls c LEFT JOIN call_billing b ON b.id=c.id "
        "WHERE c.day>=? AND c.state IN ('completed','completed_with_error')", (since,),
    ).fetchall()
    for day, kind, amount, raw in rows:
        route = json.loads(raw).get("billing_context", {}).get("route") if raw else None
        if route is None:
            # The owner's October 23 audit verified that day's relay-first route.
            route = "official" if kind.endswith("_official_fallback") or kind in {"web_search", "web_fetch"} else "relay"
        if route == "relay":
            result[day] = result.get(day, 0.0) + amount
    return result


class RelayBilling:
    def __init__(self, store, request=fetch_usage):
        self.store, self.request = store, request
        self.next_sync = 0.0

    async def sync(self, cfg):
        now = time.time()
        if not cfg.get("relay_billing_sync") or cfg.get("model_route") != "relay" or now < self.next_sync:
            return False
        self.next_sync = now + 30
        db = self.store.db
        if db.execute("SELECT 1 FROM calls WHERE state='reserved' LIMIT 1").fetchone():
            return False
        watermark = db.execute("SELECT COALESCE(MAX(rowid),0) FROM calls").fetchone()[0]
        since = cfg["relay_billing_since"]
        baseline = relay_estimates(db, since)
        previous = self.store.get("relay_billing_snapshot", {})
        try:
            usage, billing = await asyncio.to_thread(self.request, cfg)
            if usage.get("unit") not in {"USD", "CNY"}:
                raise ValueError("Unknown billing currency")
            rate = cfg["relay_credit_cny"] if usage["unit"] == "USD" else 1.0
            if not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
                raise ValueError("A verified credit conversion is required")
            # Calls continue while HTTP runs. Reconcile at an idle boundary so a
            # new completion is never both added locally and included remotely.
            if watermark != db.execute("SELECT COALESCE(MAX(rowid),0) FROM calls").fetchone()[0]:
                return False
            if db.execute("SELECT 1 FROM calls WHERE state='reserved' LIMIT 1").fetchone():
                return False
            days = dict(previous.get("days", {}))
            if not isinstance(usage.get("daily_usage"), list):
                raise ValueError("Daily billed statistics are missing")
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            for item in usage["daily_usage"]:
                day = item["date"]
                datetime.strptime(day, "%Y-%m-%d")
                if day < since or day > today:
                    continue
                amount = item["actual_cost"]
                if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
                    raise ValueError("Invalid billed amount")
                days[day] = dict(actual_cny=amount * rate, local_at_sync=baseline.get(day, 0.0),
                                 requests=item["requests"])
            snapshot = dict(days=days, synced_at=time.time(), unit=usage["unit"], cny_per_unit=rate,
                            peak_rate_enabled=billing.get("peak_rate_enabled"), error=None)
            self.store.set("relay_billing_snapshot", snapshot)
            return True
        except Exception as exc:
            # A failed read does not mean zero consumption. Keep the last bill
            # plus subsequent known local costs, and make the stale read visible.
            previous["error"] = type(exc).__name__
            self.store.set("relay_billing_snapshot", previous)
            return False
