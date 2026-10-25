"""Shared proactive limits; explicit recipient choices override defaults."""

import random
from datetime import datetime

from policy import day_start
from store import TZ


def contact_cap(cfg, contact, preference):
    if preference.get("daily_cap") == 0:
        return 0
    maximum = int(cfg.get("proactive_max", 5) if contact == cfg["owner_qq"] else cfg.get("friend_proactive_daily_max", 3))
    explicit = preference.get("explicit_cap")
    return max(0, min(maximum, explicit) if explicit is not None else maximum)


def friend_target(store, cfg, now):
    day = datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")
    key = f"friend_target:{cfg['bot_qq']}:{day}"
    target = store.get(key)
    if target is None:
        target = random.randint(3, 4)
        store.set(key, target)
    return target


def contacted_friends(store, now):
    return {row[0] for row in store.db.execute(
        "SELECT DISTINCT contact FROM actions WHERE kind='friend_proactive' AND created>=? "
        "AND state IN ('planned','sending','succeeded','unknown','acknowledged_unknown')",
        (day_start(datetime.fromtimestamp(now, TZ)),),
    )}


def unanswered_wait(store, contact, owner, now):
    last = max(store.last_action(contact, "proactive"), store.last_action(contact, "friend_proactive"))
    # Unanswered conversations get much less frequent new-topic opportunities.
    return last > store.last_incoming(contact) and now - last < (24 if owner else 48) * 3600


def global_spacing(store, cfg, now):
    last = store.db.execute(
        "SELECT COALESCE(MAX(created),0) FROM actions WHERE kind IN ('proactive','friend_proactive') "
        "AND state IN ('sending','succeeded','unknown','acknowledged_unknown')"
    ).fetchone()[0]
    return now - last >= cfg.get("proactive_global_gap_minutes", 0) * 60


def assessment_due(store, cfg, kind, now, reconnected=False):
    key = f"outreach_due:{cfg['bot_qq']}:{kind}"
    due = store.get(key)
    if reconnected or due is None:
        store.set(key, now + random.randint(5, 12) * 60)
        return False
    if now < due:
        return False
    low, high = (20, 35) if kind == "owner" else (15, 30)
    store.set(key, now + random.randint(low, high) * 60)
    return True
