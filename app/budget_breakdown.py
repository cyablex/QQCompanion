"""Group the existing local ledger by purpose without changing its accounting."""

import json

LABELS = {
    "chat": "私聊与主动联系",
    "group": "群聊",
    "memory": "记忆整理",
    "qzone": "空间互动与动态",
    "web": "联网搜索与网页阅读",
    "reading": "自主阅读与创作",
    "drawing": "绘画",
    "images": "私聊图片理解",
    "profile": "资料与签名",
    "novelty": "内容去重检查",
    "other": "其他调用",
}
KNOWN = {"completed", "completed_with_error"}


def read_budget(db, day):
    rows = db.execute(
        "SELECT day,kind,state,COALESCE(SUM(amount),0),COUNT(*) FROM calls WHERE month=? GROUP BY day,kind,state",
        (day[:7],),
    ).fetchall()
    snapshot = {}
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'").fetchone():
        row = db.execute("SELECT value FROM state WHERE key='relay_billing_snapshot'").fetchone()
        if row:
            snapshot = json.loads(row[0])
    return summarize(rows, day, snapshot)


def period_totals(rows, usage_estimate):
    """Only attribute prices when the request saved its own billing snapshot."""
    labels = {"official_peak": "官方 · 高峰", "official_offpeak": "官方 · 空闲",
              "official_per_request": "官方工具 · 按次计费",
              "relay_fixed_unverified_schedule": "中转 · 当前固定估价"}
    amounts = {}
    for amount, raw in rows:
        details = json.loads(raw)
        context = details.get("billing_context", {})
        key = context.get("route", "") + "_" + context.get("period", "")
        if key in labels:
            amounts[key] = amounts.get(key, 0.0) + amount
    unclassified = max(0, usage_estimate - sum(amounts.values()))
    result = [dict(id=key, label=labels[key], amount=round(value, 6)) for key, value in amounts.items()]
    if unclassified > 0.0000005:
        result.append(dict(id="unclassified", label="旧记录或按次工具 · 未分时段", amount=round(unclassified, 6)))
    return result


def category(kind):
    kind = kind.removesuffix("_official_fallback")
    if kind in {"reply", "proactive", "friend_proactive", "chat_style_review"}:
        return "chat"
    if kind.startswith("group_"):
        return "group"
    if kind == "conversation_memory":
        return "memory"
    if kind.startswith("qzone_"):
        return "qzone"
    if kind in {"web_search", "web_fetch", "reading_catalog_fetch"}:
        return "web"
    if kind.startswith("digital_activity"):
        return "reading"
    if kind.startswith("drawing"):
        return "drawing"
    if kind.startswith("image_understanding"):
        return "images"
    if kind in {"self_profile", "self_signature"}:
        return "profile"
    if kind == "public_novelty":
        return "novelty"
    return "other"


def summarize(rows, day, snapshot=None):
    groups = {}
    today = validation = month = unconfirmed = 0.0
    usage_estimate = month_usage_estimate = validation_usage_estimate = 0.0
    validation_calls = 0
    active_reserve = month_active_reserve = validation_active_reserve = 0.0
    unconfirmed_calls = 0
    for row_day, kind, state, amount, count in rows:
        known = state in KNOWN
        active = state == "reserved"
        if known or active:
            month += amount
        if active:
            month_active_reserve += amount
        if known:
            month_usage_estimate += amount
        if row_day != day:
            continue
        if kind.startswith(("validation_", "acceptance_")):
            if known or active:
                validation += amount
            validation_calls += count
            if known:
                validation_usage_estimate += amount
            elif active:
                validation_active_reserve += amount
            continue
        if known or active:
            today += amount
        key = category(kind)
        group = groups.setdefault(key, dict(id=key, label=LABELS[key], amount=0.0, calls=0, unconfirmed=0.0, usage_estimate=0.0, unconfirmed_calls=0))
        if known or active:
            group["amount"] += amount
        group["calls"] += count
        if known:
            group["usage_estimate"] += amount
            usage_estimate += amount
        elif active:
            active_reserve += amount
        else:
            group["unconfirmed"] += amount
            group["unconfirmed_calls"] += count
            unconfirmed += amount
            unconfirmed_calls += count
    snapshot = snapshot or {}
    adjustment = month_adjustment = 0.0
    for row_day, item in snapshot.get("days", {}).items():
        if row_day[:7] != day[:7]:
            continue
        value = item["actual_cny"] - item["local_at_sync"]
        month_adjustment += value
        if row_day == day:
            adjustment = value
    if validation_calls:
        # A key-level aggregate cannot allocate a bill difference to validation
        # versus ordinary calls. Apply it to the month only, preserving exemption.
        adjustment = 0.0
    usage_estimate += adjustment
    today += adjustment
    month += month_adjustment
    month_usage_estimate += month_adjustment
    if abs(adjustment) > .0000005:
        groups["reconciliation"] = dict(id="reconciliation", label="中转账单校准（用途未分配）",
                                        amount=adjustment, usage_estimate=adjustment, calls=0, unconfirmed=0.0, unconfirmed_calls=0)
    for group in groups.values():
        group["amount"] = round(group["amount"], 6)
        group["unconfirmed"] = round(group["unconfirmed"], 6)
        group["usage_estimate"] = round(group["usage_estimate"], 6)
    return dict(
        today=round(today, 4), validation=round(validation, 4), month=round(month, 4),
        day=day, breakdown=sorted(groups.values(), key=lambda row: (-row["amount"], row["id"])),
        unconfirmed=round(unconfirmed, 6), validation_calls=validation_calls,
        usage_estimate=round(usage_estimate, 6), month_usage_estimate=round(month_usage_estimate, 6),
        month_unconfirmed=round(sum(amount for _, _, state, amount, _ in rows if state not in KNOWN and state != "reserved"), 6),
        validation_usage_estimate=round(validation_usage_estimate, 6),
        validation_unconfirmed=round(sum(amount for row_day, kind, state, amount, _ in rows if row_day == day and kind.startswith(("validation_", "acceptance_")) and state not in KNOWN and state != "reserved"), 6),
        active_reserve=round(active_reserve, 6), month_active_reserve=round(month_active_reserve, 6),
        validation_active_reserve=round(validation_active_reserve, 6), unconfirmed_calls=unconfirmed_calls,
        billing_sync=dict(synced_at=snapshot.get("synced_at"), error=snapshot.get("error"),
                          peak_rate_enabled=snapshot.get("peak_rate_enabled"),
                          cny_per_unit=snapshot.get("cny_per_unit"),
                          relay_today=snapshot.get("days", {}).get(day, {}).get("actual_cny"),
                          adjustment=round(adjustment, 6)),
        limit_today=today, limit_validation=validation, limit_month=month,
    )
