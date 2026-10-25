"""Explicit chat limits and the owner's exact daily farewell."""

from datetime import datetime

from store import TZ
from settings import quota_unlimited

QUOTA_FAREWELL = """呜呜……今天大肥鱼已经聊满额度啦(´；ω；`)
cy连白米饭都快买不起了，大饿鱼要赶紧下线了，拜拜咯……
等cy明天攒够白米饭再来找你玩哦～🐟🍚"""


def day_start(now=None):
    now = datetime.now(TZ) if now is None else now
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def contact_limit(cfg, contact):
    if contact == cfg["owner_qq"] or quota_unlimited(cfg):
        return None
    return int(
        cfg.get("contact_daily_messages", {}).get(
            contact, cfg.get("guest_daily_messages", 50)
        )
    )
