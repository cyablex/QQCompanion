"""Public self-expression via the same NapCat APIs used by qqprofile."""

import asyncio
import json
import re
from datetime import datetime

from personality import build_personality
from public_novelty import is_novel, recent_public
from store import TZ

# Codes checked against the installed NapCat action schema; sleep is 1016.
STATUSES = {"在线": (10, 0), "元气满满": (10, 1058), "悠哉哉": (10, 1059), "想静静": (10, 1061), "学习中": (10, 1018), "睡觉中": (10, 1016)}
SIGNATURE_POLICY_VERSION = 2
SIGNATURE_MIN_AGE = 30 * 86400
SIGNATURE_REVIEW_INTERVAL = 7 * 86400


class SelfProfile:
    def __init__(self, plugin):
        self.plugin, self.store = plugin, plugin.store
        self.lock = asyncio.Lock()

    def key(self, cfg):
        return f"self_profile:{cfg['bot_qq']}"

    @staticmethod
    def active(cfg):
        return cfg.get("live") and not cfg.get("paused") and cfg.get("autonomous_profile")

    async def write(self, cfg, kind, value, now, manual=False, guard=None):
        async with self.plugin.engine.send_lock:
            fresh = self.plugin.engine.config_reader()
            if not self.active(fresh) or fresh["revision"] != cfg["revision"]:
                return
            await self.plugin.check_account()
            fresh = self.plugin.engine.config_reader()
            if not self.active(fresh) or fresh["revision"] != cfg["revision"]:
                return False
            if guard and not guard():
                return False
            state = self.store.get(self.key(cfg), {})
            if not manual and state.get(kind) == value:
                return False
            if manual:
                pass  # Explicit owner requests do not use the autonomous cadence.
            elif kind == "status":
                if now - state.get("status_attempt", 0) < 3600:
                    return False
            else:
                if state.get("signature_policy_version") == SIGNATURE_POLICY_VERSION:
                    anchor = max(state.get("signature_confirmed_at", 0), state.get("signature_stable_since", 0))
                    if now - anchor < SIGNATURE_MIN_AGE:
                        return False
                if state.get("signature_policy_attempt") == SIGNATURE_POLICY_VERSION and now - state.get("signature_attempt", 0) < 86400:
                    return False
                state["signature_policy_attempt"] = SIGNATURE_POLICY_VERSION
            state[kind + "_attempt"] = now
            state[kind + "_pending"] = value
            self.store.set(self.key(cfg), state)
            action = "set_online_status" if kind == "status" else "set_self_longnick"
            args = dict(status=STATUSES[value][0], ext_status=STATUSES[value][1], battery_status=100) if kind == "status" else dict(longNick=value)
            try:
                await asyncio.wait_for(self.plugin.bot.call_action(action, **args), 15)
                await asyncio.sleep(1)
                if kind == "status":
                    observed = await asyncio.wait_for(self.plugin.bot.call_action("nc_get_user_status", user_id=str(cfg["bot_qq"])), 10)
                    verified = (observed.get("status"), observed.get("ext_status")) == STATUSES[value]
                else:
                    observed = await asyncio.wait_for(self.plugin.bot.call_action("get_stranger_info", user_id=int(cfg["bot_qq"]), no_cache=True), 10)
                    verified = observed.get("long_nick", observed.get("longNick")) == value
                state = self.store.get(self.key(cfg), {})
                state[kind + "_result"] = "confirmed" if verified else "unconfirmed"
                if verified:
                    if kind == "signature":
                        history = list(state.get("signature_history", []))
                        if state.get("signature") and not any(row["text"] == state["signature"] for row in history):
                            history.append(dict(text=state["signature"], created=state.get("signature_confirmed_at", now)))
                        state["signature_history"] = (history + [dict(text=value, created=now)])[-14:]
                        state["signature_confirmed_at"] = now
                        state["signature_policy_version"] = SIGNATURE_POLICY_VERSION
                        state["signature_stable_since"] = now
                    state[kind] = value
                    state.pop(kind + "_pending", None)
                self.store.set(self.key(cfg), state)
                return verified
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                state = self.store.get(self.key(cfg), {})
                state[kind + "_result"] = type(exc).__name__
                self.store.set(self.key(cfg), state)
                return False

    async def review_signature(self, cfg, stamp):
        state = self.store.get(self.key(cfg), {})
        if state.get("signature_policy_version") == SIGNATURE_POLICY_VERSION:
            anchor = max(state.get("signature_confirmed_at", 0), state.get("signature_stable_since", 0))
            if stamp - anchor < SIGNATURE_MIN_AGE:
                return
        if stamp < state.get("signature_next_review", 0):
            return
        state["signature_next_review"] = stamp + SIGNATURE_REVIEW_INTERVAL
        self.store.set(self.key(cfg), state)
        payload = dict(current_signature=state.get("signature", ""),
                       previous_signatures=[row["text"] for row in state.get("signature_history", [])[-3:]])
        system = build_personality(cfg, False, public=True) + (
            "这是一项独立的QQ长期签名选择，不是动态、日记、读书笔记，也不是即时心情。"
            "签名表达你长期喜欢的句子、稳定的性格、审美或生活态度，可以用很久。"
            "程序已保证至少保留30天，到期只是给你一次考虑机会，没有必须更换的任务。"
            "默认keep；只有新句子确实比原来更能代表自己、愿意长期挂着时才update。"
            "若当前签名像近期阅读摘要或活动进度，这次将它改为长期的自我表达。"
            "不要写今天/刚刚做了什么、读到的具体知识点、作品进度或接下来的计划，"
            "不要把动态换词抄进签名，不追求每次新鲜，不编造现实经历。"
            "不要出现具体联系人、QQ号、私聊或私人关系，不抄其他好友签名；可以表达虚拟角色的喜好。"
            "中文60字以内，一句简洁自然、不堆砌辞藻的话；不用引号包裹，不必自报名字。"
            '只输出JSON：{"action":"keep"}或{"action":"update","signature":"长期愿意保留的一句话"}。'
        )
        try:
            raw = await self.plugin.engine.generate_aux("self_signature", system, [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
            value = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            fresh = self.plugin.engine.config_reader()
            if not self.active(fresh) or fresh["revision"] != cfg["revision"]:
                return
            if not isinstance(value, dict):
                raise ValueError("Invalid signature decision")
            if value.get("action") == "keep":
                state = self.store.get(self.key(cfg), {})
                if state.get("signature_policy_version") != SIGNATURE_POLICY_VERSION:
                    state["signature_policy_version"] = SIGNATURE_POLICY_VERSION
                    state["signature_stable_since"] = stamp
                self.store.set(self.key(cfg), state)
                return
            signature = value.get("signature", "")
            if value.get("action") != "update" or not isinstance(signature, str):
                return
            signature = signature.strip()
            if (not 0 < len(signature) <= 60 or re.search(r"\d{5,}|https?://|[\r\n\x00-\x1f]", signature)
                    or any(str(cfg.get(k, "")) in signature for k in ("owner_qq", "bot_qq") if cfg.get(k))):
                return
            if signature == state.get("signature"):
                state = self.store.get(self.key(cfg), {})
                state.setdefault("signature_stable_since", stamp)
                state["signature_policy_version"] = SIGNATURE_POLICY_VERSION
                self.store.set(self.key(cfg), state)
                return
            # Keep the existing cross-post duplication check out of the writer's
            # prompt: recent activities should not supply signature material.
            if await is_novel(self.plugin.engine, cfg, dict(kind="signature", text=signature), recent_public(self.store, cfg)):
                confirmed = await self.write(cfg, "signature", signature, stamp)
                if not confirmed:
                    state = self.store.get(self.key(cfg), {})
                    state["signature_next_review"] = stamp + 86400
                    self.store.set(self.key(cfg), state)
        except asyncio.CancelledError:
            raise
        except Exception:
            state = self.store.get(self.key(cfg), {})
            state["signature_next_review"] = stamp + 86400
            self.store.set(self.key(cfg), state)
            raise

    async def run(self, now=None):
        cfg = self.plugin.engine.config_reader()
        if not self.active(cfg) or self.lock.locked():
            return
        now = datetime.now(TZ) if now is None else now
        stamp = now.timestamp()
        async with self.lock:
            state = self.store.get(self.key(cfg), {})
            if now.hour < 8:
                await self.write(cfg, "status", "睡觉中", stamp)
                return
            if state.get("status") == "睡觉中":
                await self.write(cfg, "status", "在线", stamp)
            if stamp - state.get("assessed", 0) < 3600:
                await self.review_signature(cfg, stamp)
                return
            state = self.store.get(self.key(cfg), {})
            state["assessed"] = stamp
            self.store.set(self.key(cfg), state)
            mood = self.store.get(f"self_mood:{cfg['bot_qq']}", {})
            if stamp - mood.get("at", 0) > 86400:
                mood = {}
            payload = dict(time=now.isoformat(timespec="minutes"), mood=mood.get("mood", "平静"), previous_status=state.get("status", "在线"))
            payload["public_activities"] = self.plugin.engine.activities.context(cfg["owner_qq"], cfg, public=True)
            payload["public_drawings"] = self.plugin.engine.sketchbook.context(cfg["owner_qq"], cfg, public=True)
            system = build_personality(cfg, False, public=True) + (
                "你可以根据自己的心情和真实公开活动调整自己的QQ在线状态，也可以保留原样。"
                "这里只提供不含私聊细节的心情标签和公开活动，不猜测其他人近况，不写任何具体联系人的名字、QQ号、私事或私人关系。"
                "只输出JSON：{\"action\":\"update\",\"status\":\"悠哉哉\"} 或 {\"action\":\"keep\"}。"
                "状态只能从在线/元气满满/悠哉哉/想静静/学习中选择。学习中需要确有阅读依据。"
                "这项任务不能修改签名，签名由另一个长期策略维护。"
            )
            raw = await self.plugin.engine.generate_aux("self_profile", system, [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}])
            value = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
            if isinstance(value, dict) and value.get("action") == "update":
                status = value.get("status")
                if isinstance(status, str) and status in STATUSES and status != "睡觉中":
                    await self.write(cfg, "status", status, stamp)
            await self.review_signature(cfg, stamp)
