"""Optional, recipient-specific conversation; daily limits are ceilings."""

import random
import time
from datetime import datetime

from engine import quiet
from policy import contact_limit, day_start
from settings import read_settings, quota_unlimited
from store import CHAT_KINDS, TZ
from outreach_policy import contact_cap, contacted_friends, friend_target, global_spacing, unanswered_wait


class FriendProactive:
    def __init__(self, plugin):
        self.plugin, self.store = plugin, plugin.store

    def eligible(self, contact, cfg, now):
        if (
            not contact.isdigit()
            or contact in {cfg["owner_qq"], cfg["bot_qq"]}
            or contact not in self.store.get("known_friend_ids", [])
            or not cfg.get("proactive")
            or not cfg.get("friend_proactive")
            or quiet(datetime.fromtimestamp(now, TZ))
            or not self.plugin.engine.allowed(cfg, contact)
            or contact in self.plugin.workers
            or self.store.has_unknown(contact)
        ):
            return False
        last_in = self.store.last_incoming(contact)
        if not last_in:
            return False
        last_out = self.store.last_action(contact, "friend_proactive")
        assessment = self.store.get("friend_proactive_assessment:" + contact, {})
        pref = self.plugin.engine.memory.preference(contact, now)
        cap = contact_cap(cfg, contact, pref)
        if cap <= 0 or pref["blocked_until"] > now:
            return False
        if (
            (last_in and now - last_in < 30 * 60)
            or now - last_out < cfg.get("friend_proactive_interval_hours", 1.5) * 3600
            or unanswered_wait(self.store, contact, False, now)
            or not global_spacing(self.store, cfg, now)
            or (self.plugin.engine.memory._state(contact, now) or {}).get("kind") == "away"
            or now - self.store.last_action(contact, "reply") < 30 * 60
            or (self.store.get("friend_proactive_next:" + contact, 0) > now
                and last_in <= assessment.get("incoming", last_in))
        ):
            return False
        latest = self.store.history(contact, limit=1)
        # A still-unanswered incoming message should be handled by the reply queue.
        if latest and latest[-1]["role"] != "assistant":
            return False
        contacts = contacted_friends(self.store, now)
        if contact not in contacts and len(contacts) >= friend_target(self.store, cfg, now):
            return False
        if (
            self.store.action_count(
                "friend_proactive", day_start(datetime.fromtimestamp(now, TZ)), contact
            )
            >= cap
        ):
            return False
        if quota_unlimited(cfg):
            return True
        return self.store.sent_count(
            day_start(datetime.fromtimestamp(now, TZ)), contact
        ) < contact_limit(cfg, contact) and self.store.sent_count(
            day_start(datetime.fromtimestamp(now, TZ)), kinds=CHAT_KINDS
        ) < cfg.get("daily_chat_messages", 500)

    async def run(self, now=None):
        now = time.time() if now is None else now
        cfg = read_settings()
        candidates = [
            str(c)
            for c in self.store.get("known_friend_ids", [])
            if self.eligible(str(c), cfg, now)
        ]
        if not candidates:
            return False
        contacts = contacted_friends(self.store, now)
        volumes = dict(self.store.db.execute(
            "SELECT contact,COUNT(*) FROM messages WHERE role='user' AND created>=? GROUP BY contact",
            (now - 7 * 86400,)))
        random.shuffle(candidates)
        contact = min(candidates, key=lambda c: (
            c in contacts,
            self.store.get("friend_proactive_assessment:" + c, {}).get("at", 0),
            self.store.last_action(c, "friend_proactive"),
            volumes.get(c, 0)))
        from outreach import assess
        return await assess(self.plugin.engine,
            contact,
            cfg,
            "friend_proactive",
            "这是一次可选的主动联系机会，没有每天必须找几个人的任务；不必等重大成果，有自然的共同话题就可聊两句。"
            "先问自己这位好友真的会想收到吗：有与你们实际相处有关的具体小趣味、自然的心情，"
            "或特别适合这位好友的画面时才reply。脱离你们的对话也能群发的话、没事找事的问候和硬接问题就skip。"
            "阅读主要是自己的兴趣，不主动广播每个知识点和一点点进展；只有对方明确表现过相关兴趣，"
            "而且这次发现本身确实值得分享，才简短聊。不能假定所有人爱听鲸鱼，也不汇报研究计划。"
            "如果最近主要是你在讲，对方只有嗯、哦、收到等短回应，不算共同兴趣；这轮宁可skip，别换个泛泛关心继续打扰。"
            "聊得少也可以自然主动：根据对方确实表达过的一点喜好或友好交流，分享一件具体、轻松的小事，"
            "不必等到很熟、有很多共同故事才联系；熟悉程度按真实记录，不硬套亲密，仍然允许没话题就skip。"
            "画画不需要科普目的：可以因为想表达轻松、开心而画一张；关系确实友好熟悉时，"
            "画两个开心伙伴，轻轻说像你们聊天的感觉也可以，不夸大亲密或假装见过面。"
            "没有想发的就安静地做自己的事，不为了发图硬找人。不要为了开启聊天试探对方今天空不空，"
            "别根据几天前忙就猜今天不忙，不催回复、不追问为什么没理你。"
            "不编造对方的近况、约定、共同经历或未完成的活动；不提任何其他人的私聊。"
            "对方曾要求不主动打扰或拒绝此话题时必须 skip。有话才 reply 一条简短消息。",
            now,
        )
