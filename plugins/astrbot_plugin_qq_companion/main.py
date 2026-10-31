"""AstrBot adapter for the local, bounded QQ companion engine."""

import asyncio
import os
import random
import sys
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star

ROOT = Path(os.environ["QQCOMPANION_ROOT"])
sys.path.insert(0, str(ROOT / "app"))

from autopost import AutoPosts  # noqa: E402
from conversation_followups import ConversationFollowups  # noqa: E402
from engine import DeliveryUnavailable, Engine, TaskStateChanged  # noqa: E402
from friend_proactive import FriendProactive  # noqa: E402
from group_chat import GroupChats  # noqa: E402
from media import MediaPipeline  # noqa: E402
from offline_recovery import OfflineRecovery, source_key  # noqa: E402
from outreach_policy import assessment_due  # noqa: E402
from own_space import OwnSpace  # noqa: E402
from qq_faces import segments as face_segments  # noqa: E402
from qq_readiness import check_messaging  # noqa: E402
from reply_queue import ReplyQueue  # noqa: E402
from self_profile import SelfProfile  # noqa: E402
from settings import atomic_json, read_settings  # noqa: E402
from model_billing import billing_snapshot  # noqa: E402
from relay_billing import RelayBilling  # noqa: E402
from social import Social  # noqa: E402
from stickers import StickerLibrary  # noqa: E402
from store import BudgetExceeded, Store  # noqa: E402
from local_voice import LocalVoice, allowed_file as voice_allowed_file  # noqa: E402
from singing import Singing  # noqa: E402
from turn_actions import TurnActions  # noqa: E402
from vpet_bridge import VPetBridge, DELIVERY  # noqa: E402
from daily_diary import DailyDiary  # noqa: E402


class QQCompanion(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.context = context
        self.bot = None
        self.store = Store(ROOT / "data" / "companion.sqlite3")
        self.engine = Engine(self.store, self.generate, self.send_private)
        self.reply_queue = ReplyQueue(self.store)
        self.pending = {}
        self.workers = {}
        self.loop_task = None
        self.reply_recovery_task = None
        self.social = Social(self)
        self.stickers = StickerLibrary()
        self.media = MediaPipeline(self)
        self.engine.prepare = self.media.prepare
        self.engine.stickers = self.stickers
        self.engine.voice = LocalVoice(self.store, managed=True)
        self.autopost = AutoPosts(self)
        self.offline = OfflineRecovery(self)
        self.offline_task = None
        self.own_space = OwnSpace(self)
        self.own_space_task = None
        self.qzone_task = None
        self.groups = GroupChats(self)
        self.singing = Singing(self)
        self.engine.singing = self.singing
        self.groups.engine.singing = self.singing
        self.singing_task = None
        self.friend_proactive = FriendProactive(self)
        self.followups = ConversationFollowups(self)
        self.memory_tasks = {}
        self.activity_task = None
        self.followup_task = None
        self.memory_cursor = 0
        self.self_profile = SelfProfile(self)
        self.turn_actions = TurnActions(self)
        self.engine.turn_actions = self.groups.engine.turn_actions = self.turn_actions
        self.expression_task = None
        self.vpet = VPetBridge(self)
        self.daily_diary = DailyDiary(self.store)
        self.engine.delivery_router = self.vpet.route_delivery

    async def initialize(self):
        try:
            self.vpet.start()
        except OSError as exc:
            logger.warning('VPet bridge unavailable: %s', type(exc).__name__)
        self.loop_task = asyncio.create_task(self.scheduler())
        self.reply_recovery_task = asyncio.create_task(self.recover_replies())
        self.singing_task = asyncio.create_task(self.singing.run())
        logger.info("QQ companion loaded; actual sending is controlled by local policy")

    async def generate(
        self, system, contexts, response_mode="json", official_fallback=False
    ):
        cfg = read_settings()
        if official_fallback:
            cfg = dict(cfg, model_route="official")
        if cfg.get("model_route", "official") == "relay" and response_mode == "json":
            # This relay accepts JSON mode but can still return ordinary prose.
            system += (
                "\n本轮是程序内部决策任务。只输出一个有效JSON对象，保留上面要求的字段，"
                'action只能是reply或skip。没有合适内容时输出{"action":"skip","messages":[],"memory":[]}。'
                "不要转为回答历史用户的问题，不要Markdown或任何JSON之外的文字。"
            )
        from astrbot.core.agent.message import (
            AssistantMessageSegment,
            ImageURLPart,
            TextPart,
            UserMessageSegment,
        )

        messages = []
        for msg in contexts:
            cls = (
                AssistantMessageSegment
                if msg["role"] == "assistant"
                else UserMessageSegment
            )
            if isinstance(msg["content"], str):
                parts = [TextPart(text=msg["content"])]
            else:
                parts = []
                for item in msg["content"]:
                    if item.get("type") == "text":
                        parts.append(TextPart(text=item["text"]))
                    elif item.get("type") == "image_url" and msg["role"] == "user":
                        parts.append(
                            ImageURLPart(
                                image_url=ImageURLPart.ImageURL(
                                    url=item["image_url"]["url"]
                                )
                            )
                        )
            messages.append(cls(content=parts))
        billing = billing_snapshot(cfg)
        result = await self.context.llm_generate(
            chat_provider_id=(
                "deepseek_official_fallback"
                if official_fallback
                else "deepseek_companion"
            )
            + ("_text" if response_mode == "text" else ""),
            system_prompt=system,
            contexts=messages,
            request_max_retries=1,
        )
        usage = result.usage
        request_id = getattr(getattr(result, "raw_completion", None), "id", None)
        if request_id:
            billing["billing_context"]["request_id"] = str(request_id)
        return result.completion_text, (
            {
                "input_other": usage.input_other,
                "input_cached": usage.input_cached,
                "output": usage.output,
                **billing,
            }
            if usage and usage.total
            else None
        )

    async def check_account(self):
        if not self.bot:
            raise RuntimeError("QQ connection not ready")
        account = await asyncio.wait_for(self.bot.call_action("get_login_info"), 8)
        if str(account.get("user_id", "")) != read_settings()["bot_qq"]:
            raise RuntimeError(
                "Connected QQ account does not match configured bot account"
            )

    async def send_private(self, contact, text, sticker_id=None, image_file=None, voice_file=None, song_file=None, song_id=None):
        if DELIVERY.get() is not None:
            media = dict(sticker_id=sticker_id, image_file=image_file, voice_file=voice_file, song_file=song_file)
            return await self.vpet.deliver(contact, text, **{key: value for key, value in media.items() if value})
        try:
            await self.check_messaging()
        except Exception as exc:
            raise DeliveryUnavailable(
                "QQ preflight unavailable; send not attempted"
            ) from exc
        cfg = read_settings()
        if song_file:
            if not cfg.get("singing_enabled", True) or not self.singing.library.allowed_file(song_id, song_file):
                raise DeliveryUnavailable("Song unavailable; send not attempted")
            return await self.bot.call_action(
                "send_private_msg", user_id=int(contact),
                message=[{"type": "record", "data": {"file": Path(song_file).as_uri()}}],
            )
        if voice_file:
            if not voice_allowed_file(cfg, contact, voice_file):
                raise DeliveryUnavailable("Voice unavailable; send not attempted")
            return await self.bot.call_action(
                "send_private_msg", user_id=int(contact),
                message=[{"type": "record", "data": {"file": Path(voice_file).as_uri()}}],
            )
        segments = (
            face_segments(text, allow_face=cfg.get("send_memes", False))
            if text.strip()
            else []
        )
        if image_file:
            if not self.engine.sketchbook.allowed_file(cfg, image_file, contact):
                raise DeliveryUnavailable(
                    "Drawing unavailable; image send not attempted"
                )
            segments.append(
                {"type": "image", "data": {"file": Path(image_file).as_uri()}}
            )
        elif (
            cfg.get("send_memes", False)
            and sticker_id
            and sticker_id in self.stickers.items
        ):
            item = self.stickers.items[sticker_id]
            segments.append(
                {
                    "type": "image",
                    "data": {"file": Path(item["file"]).as_uri(), "sub_type": 1},
                }
            )
        if not segments:
            raise DeliveryUnavailable("Empty message; send not attempted")
        return await self.bot.call_action(
            "send_private_msg",
            user_id=int(contact),
            message=segments,
        )

    async def check_messaging(self):
        cfg = read_settings()
        if not self.bot:
            raise DeliveryUnavailable("QQ session unavailable")
        try:
            count = await check_messaging(self.bot.call_action, cfg["bot_qq"])
            self.store.set(
                "message_transport",
                {"ready": True, "checked": time.time(), "friends": count},
            )
        except Exception as exc:
            self.store.set(
                "message_transport",
                {"ready": False, "checked": time.time(), "reason": type(exc).__name__},
            )
            raise

    def enqueue_reply(self, contact, event):
        self.pending[contact] = event
        self.reply_queue.enqueue(contact, event)
        if contact not in self.workers:
            self.workers[contact] = asyncio.create_task(self.drain(contact))
        return self.workers[contact]

    async def drain(self, contact):
        try:
            opened = time.monotonic()
            while True:
                marker = self.pending.get(contact)
                await asyncio.sleep(3)
                if (
                    marker != self.pending.get(contact)
                    and time.monotonic() - opened < 8
                ):
                    continue
                marker = self.pending.get(contact)
                job = self.reply_queue.begin(contact)
                if not job:
                    break
                try:
                    await self.engine.reply(contact)
                    self.reply_queue.finish(contact, job["event"])
                    self.schedule_memory(contact)
                except TaskStateChanged:
                    # A concurrent task result is normal activity, not a model
                    # outage. Keep the bounded reply job without a false error notice.
                    self.reply_queue.retry(contact, job['event'])
                except BudgetExceeded:
                    self.reply_queue.finish(contact, job["event"])
                    self.store.set("last_error", "Model budget exhausted")
                    await self.error_notice(
                        contact, "今天的模型调用额度已用完，额度恢复后再继续聊。"
                    )
                except Exception as exc:
                    self.store.set("last_error", type(exc).__name__)
                    logger.warning(
                        f"QQ companion reply interrupted: {type(exc).__name__}"
                    )
                    transient = isinstance(
                        exc, (TimeoutError, ConnectionError, DeliveryUnavailable)
                    ) or type(exc).__name__ in {
                        "APIConnectionError",
                        "APITimeoutError",
                        "InternalServerError",
                        "RateLimitError",
                        "EmptyModelOutputError",
                        "EmptyReplyError",
                    }
                    if transient and not self.store.has_unknown(contact):
                        self.reply_queue.retry(contact, job["event"])
                    else:
                        self.reply_queue.finish(contact, job["event"])
                    await self.error_notice(
                        contact,
                        "网络或模型暂时没响应，我会在几分钟内再试。"
                        if transient
                        else "刚才回复生成失败了，稍后再发一句我就重新回答。",
                    )
                if marker == self.pending.get(contact):
                    break
                opened = time.monotonic()
        finally:
            self.workers.pop(contact, None)

    async def error_notice(self, contact, text):
        # One notice per contact per five minutes; never retry uncertain sends.
        now = time.time()
        marker = f"error_notice:{contact}"
        if now - self.store.get(marker, 0) < 300:
            return
        self.store.set(marker, now)
        cfg = read_settings()
        result = {
            "action": "reply",
            "messages": [text],
            "revision": cfg["revision"],
            "read_through": self.store.last_incoming(contact),
        }
        try:
            await self.engine.deliver(contact, "error_notice", result)
        except Exception:
            pass

    def schedule_memory(self, contact):
        cfg = read_settings()
        if not cfg.get("memory_enabled", False) or not self.engine.allowed(
            cfg, contact
        ):
            return
        if any(not task.done() for task in self.memory_tasks.values()):
            return
        self.memory_tasks = {
            key: task for key, task in self.memory_tasks.items() if not task.done()
        }
        self.memory_tasks[contact] = asyncio.create_task(
            self.consolidate_memory(contact)
        )

    async def consolidate_memory(self, contact):
        try:
            cfg = read_settings()
            await self.engine.memory.consolidate(contact, self.engine, cfg)
            if contact == cfg['owner_qq']:
                await self.daily_diary.write(self.engine, cfg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.set("last_memory_error", type(exc).__name__)

    async def run_activity(self):
        try:
            await self.engine.activities.run(self.engine, read_settings())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.set("last_activity_error", type(exc).__name__)

    async def run_followups(self):
        try:
            await self.followups.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.set("last_followup_error", type(exc).__name__)

    async def run_expression(self):
        try:
            await self.engine.sketchbook.run(self.engine)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.set("last_drawing_error", type(exc).__name__)
        try:
            await self.self_profile.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.set("last_profile_error", type(exc).__name__)

    @filter.platform_adapter_type(filter.PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def receive(self, event):
        cfg = read_settings()
        raw = event.message_obj.raw_message
        if not isinstance(raw, dict):
            return
        if str(raw.get("self_id", "")) != cfg["bot_qq"]:
            event.stop_event()
            return
        self.bot = event.bot
        if raw.get("post_type") == "request" and raw.get("request_type") == "friend":
            event.stop_event()
            flag, uid = str(raw.get("flag", "")), str(raw.get("user_id", ""))
            if flag and uid.isdigit():
                self.store.db.execute(
                    "INSERT OR IGNORE INTO friends VALUES(?,?,?,?,?,?)",
                    (
                        cfg["bot_qq"] + ":" + flag,
                        uid,
                        flag,
                        str(raw.get("comment", ""))[:1000],
                        "pending",
                        time.time(),
                    ),
                )
            return
        if raw.get("post_type") == "message" and raw.get("message_type") == "group":
            event.stop_event()
            await self.groups.receive(raw)
            return
        if raw.get("post_type") != "message" or raw.get("message_type") != "private":
            event.stop_event()
            return
        contact = str(raw.get("user_id", ""))
        event.stop_event()
        if contact == cfg["bot_qq"] or not contact.isdigit():
            return
        if contact != cfg["owner_qq"] and not self.engine.allowed(cfg, contact):
            return
        if contact != cfg["owner_qq"] and cfg.get("guest_mode") == "open":
            if contact not in self.store.get("known_friend_ids", []):
                try:
                    await self.social.reconcile_friends()
                except Exception:
                    return
                if contact not in self.store.get("known_friend_ids", []):
                    return
        text = event.message_str.strip()
        text, attachments = MediaPipeline.describe_input(raw, text)
        if not text and not attachments:
            return
        if contact == cfg['owner_qq'] and (text in {'/待发动态', '/动态草稿'}
                or text.startswith(('/同意动态 ', '/拒绝动态 '))):
            draft_reply = self.autopost.draft_command(contact, text, source_time=raw.get('time') or time.time())
            if draft_reply is not None:
                await self.send_private(contact, draft_reply)
                return
        if contact == cfg["owner_qq"] and text in {"/发说说", "/说说状态", "/表情库"}:
            if text == "/表情库":
                answer = "已收集鲸鱼娘表情：" + "、".join(
                    item["tag"] for item in self.stickers.items.values()
                )
            elif text == "/说说状态":
                import json

                answer = (
                    json.dumps(self.store.get("last_own_post", {}), ensure_ascii=False)
                    or "还没有自主说说记录。"
                )
            else:
                try:
                    status = await self.autopost.run(force=True)
                    answer = (
                        "本次说说检查结果："
                        + status
                        + "；自动评估最多每48小时一次，没有好内容就跳过；默认先存草稿，/待发动态 查看后批准。"
                    )
                except Exception as exc:
                    self.store.set("last_error", type(exc).__name__)
                    answer = (
                        "这次说说发布未取得可确认的结果，请查看实际空间；不会自动重发。"
                    )
            await self.send_private(contact, answer)
            return
        if contact == cfg["owner_qq"]:
            try:
                approval_reply = await self.social.approval_command(
                    contact, text, source=source_key(cfg["bot_qq"], contact, raw),
                    source_time=raw.get("time") or time.time(),
                )
            except Exception as exc:
                self.store.set("last_error", type(exc).__name__)
                approval_reply = (
                    "这次评论审批未完成，请查看实际空间状态；不会自动重发评论。"
                )
            if approval_reply is not None:
                if approval_reply:
                    receipt = await self.send_private(contact, approval_reply)
                    if isinstance(receipt, dict) and receipt.get("message_id"):
                        self.store.message(contact, "assistant", approval_reply)
                return
        if contact == cfg["owner_qq"] and text == "/评论最新动态":
            if not cfg["qzone"]:
                await self.send_private(
                    contact, "空间评论还没有开启，当前不会发表公开评论。"
                )
            else:
                try:
                    await self.social.qzone(force_latest=True)
                    await self.send_private(
                        contact, "已检查最新动态，结果会按发送回执记录；不会重复评论。"
                    )
                except Exception as exc:
                    self.store.set("last_error", type(exc).__name__)
                    await self.send_private(
                        contact, "这次空间检查未完成，没有自动重试公开评论。"
                    )
            return
        command = self.engine.personal_command(contact, text)
        if command is None:
            command = self.engine.admin(contact, text)
        if command is not None:
            # Commands use the owner identity from the transport, not model output.
            if contact == cfg["owner_qq"]:
                await self.send_private(contact, command)
            else:
                # Privacy/frequency changes take effect even when the personal
                # reply quota is full; their acknowledgments still consume the
                # same ordinary message budget as other friend replies.
                await self.engine.deliver(
                    contact,
                    "reply",
                    {
                        "action": "reply",
                        "messages": [command],
                        "revision": cfg["revision"],
                        "read_through": self.store.last_incoming(contact),
                    },
                    key=f"personal_command:{cfg['bot_qq']}:{contact}:{raw.get('message_id')}",
                )
            return
        eid = f"{cfg['bot_qq']}:{contact}:{raw.get('message_id')}"
        incoming_text = text or (
            "[收到语音，等待识别]"
            if attachments[0][0] == "voice"
            else "[收到图片或表情包，等待理解]"
        )
        if not self.store.incoming(eid, contact, incoming_text[:6000]):
            return
        self.store.set("event_source:" + eid, dict(time=raw.get("time")))
        if cfg.get("memory_enabled", False):
            self.engine.memory.observe(contact)
        self.store.set(source_key(cfg["bot_qq"], contact, raw), eid)
        source_time = raw.get("time")
        delayed = isinstance(source_time, (int, float)) and 0 < source_time <= time.time() - 600
        self.store.set(f"offline_context:{contact}",
                       dict(event=eid, source_time=source_time, recovered_at=time.time()) if delayed else None)
        if attachments:
            self.media.capture(eid, contact, text[:6000], attachments)
        self.enqueue_reply(contact, eid)

    async def recover_replies(self):
        # Queue wakeups must not wait for a slow/failed autonomous model call.
        while True:
            try:
                cfg = read_settings()
                if self.bot and cfg['live'] and not cfg['paused'] and self.reply_queue.ready():
                    await self.check_messaging()
                    self.resume_replies()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.set('reply_recovery_error', type(exc).__name__)
            await asyncio.sleep(10)

    def resume_replies(self):
        self.groups.resume_pending()
        for contact in self.reply_queue.ready():
            if not contact.startswith('group:') and contact not in self.workers:
                self.pending[contact] = self.store.get('reply_job:' + contact)['event']
                self.workers[contact] = asyncio.create_task(self.drain(contact))

    async def scheduler(self):
        billing = RelayBilling(self.store)
        next_backup = time.time()
        next_friend_probe = time.time()
        next_emote_collection = time.time()
        next_own_post = time.time() + random.randint(10, 30) * 60
        next_offline = 0
        was_connected = False
        next_own_space = 0
        next_activity = time.time() + 90
        next_memory = time.time() + 30
        next_expression = time.time() + 15
        while True:
            try:
                self.turn_actions.sync()
                if not self.bot:
                    for platform in self.context.platform_manager.platform_insts:
                        if platform.meta().id == "qq_companion":
                            self.bot = getattr(platform, "bot", None)
                if self.bot:
                    try:
                        await self.check_messaging()
                        connected = True
                    except Exception:
                        connected = False
                else:
                    connected = False
                atomic_json(
                    ROOT / "data/health.json",
                    {
                        "updated": time.time(),
                        "qq_connected": connected,
                        "transport_ready": connected,
                        **self.store.status(),
                        "last_error": self.store.get("last_error", ""),
                        "official_fallback": self.store.get(
                            "official_fallback_stats", {}
                        ),
                        "emote_collection": self.store.get("emote_collection", {}),
                        "last_own_post": self.store.get("last_own_post", {}),
                        "last_media_error": self.store.get("last_media_error", ""),
                        "offline_recovery": self.store.get(
                            "offline_recovery_report", {}
                        ),
                        "own_space_replies": self.store.get("own_space_report", {}),
                        "groups": self.store.get("group_status", {}),
                        "conversation_version": 2,
                        "memory_enabled": read_settings().get("memory_enabled", False),
                        "last_memory_error": self.store.get("last_memory_error", ""),
                        "last_activity_error": self.store.get(
                            "last_activity_error", ""
                        ),
                    },
                )
                if not connected:
                    next_offline = 0
                    next_own_space = 0
                if connected:
                    self.resume_replies()
                    if time.time() >= next_expression:
                        next_expression = time.time() + 600
                        if not self.workers and (
                            not self.expression_task or self.expression_task.done()
                        ):
                            self.expression_task = asyncio.create_task(
                                self.run_expression()
                            )
                    await billing.sync(read_settings())
                    await self.groups.probe()
                if connected and time.time() >= next_own_space:
                    next_own_space = time.time() + 1800
                    if not self.own_space_task or self.own_space_task.done():
                        self.own_space_task = asyncio.create_task(
                            self.check_own_space()
                        )
                if connected and (not was_connected or time.time() >= next_offline):
                    next_offline = time.time() + 300
                    if not self.offline_task or self.offline_task.done():
                        self.offline_task = asyncio.create_task(self.recover_offline())
                just_connected = connected and not was_connected
                was_connected = connected
                outreach_cfg = read_settings()
                if connected and assessment_due(
                    self.store, outreach_cfg, "owner", time.time(), just_connected
                ):
                    await self.engine.proactive()
                if connected and assessment_due(
                    self.store, outreach_cfg, "friends", time.time(), just_connected
                ):
                    try:
                        await self.friend_proactive.run()
                    except Exception as exc:
                        self.store.set("friend_proactive_error", type(exc).__name__)
                if connected:
                    if not self.followup_task or self.followup_task.done():
                        self.followup_task = asyncio.create_task(self.run_followups())
                    if time.time() >= next_activity:
                        next_activity = time.time() + 3600
                        if not self.workers and (
                            not self.activity_task or self.activity_task.done()
                        ):
                            self.activity_task = asyncio.create_task(
                                self.run_activity()
                            )
                    if time.time() >= next_memory and not self.workers:
                        next_memory = time.time() + 30
                        recent = self.store.db.execute(
                            "SELECT contact FROM messages WHERE role='user' GROUP BY contact ORDER BY MAX(created) DESC LIMIT 20"
                        ).fetchall()
                        contacts = [
                            row[0]
                            for row in recent
                            if row[0].isdigit()
                            and self.engine.allowed(read_settings(), row[0])
                        ]
                        if contacts:
                            self.memory_cursor %= len(contacts)
                            self.schedule_memory(contacts[self.memory_cursor])
                            self.memory_cursor += 1
                    if time.time() >= next_emote_collection and read_settings().get(
                        "send_memes", False
                    ):
                        next_emote_collection = time.time() + 3600
                        try:
                            await self.stickers.collect(self)
                        except Exception as exc:
                            self.store.set("emote_collection_error", type(exc).__name__)
                    if time.time() >= next_own_post or self.store.get(
                        "own_post_requested", False
                    ):
                        self.store.set("own_post_requested", False)
                        next_own_post = time.time() + random.randint(60, 120) * 60
                        try:
                            self.store.set(
                                "last_autopost_check", await self.autopost.run()
                            )
                        except Exception as exc:
                            self.store.set("last_autopost_error", type(exc).__name__)
                    await self.social.comments.resume()
                    try:
                        await self.autopost.resume()
                        await self.autopost.notify_drafts()
                    except Exception as exc:
                        self.store.set('last_autopost_error', type(exc).__name__)
                    await self.social.notify_approvals()
                    if time.time() >= next_friend_probe:
                        next_friend_probe = time.time() + 300
                        try:
                            await self.social.reconcile_friends()
                        except Exception as exc:
                            self.store.set("friend_error", type(exc).__name__)
                    if self.store.get("qzone_probe_requested", False):
                        self.store.set("qzone_probe_requested", False)
                        try:
                            report = await self.social.qzone(
                                read_only=True, establish_baseline=True
                            )
                        except Exception as exc:
                            report = {
                                "readable": False,
                                "error_class": type(exc).__name__,
                            }
                        atomic_json(ROOT / "data/qzone-probe.json", report)
                    if self.store.get("qzone_comment_test_requested", False):
                        self.store.set("qzone_comment_test_requested", False)
                        await self.social.qzone(force_latest=True)
                    await self.social.friends()
                    if self.social.daily_due() and (
                        not self.qzone_task or self.qzone_task.done()
                    ):
                        self.qzone_task = asyncio.create_task(self.check_friend_space())
                if time.time() >= next_backup:
                    import sqlite3
                    from datetime import datetime

                    stamp = datetime.now().strftime("%Y%m%d")
                    with sqlite3.connect(
                        ROOT / f"backups/companion-{stamp}.sqlite3"
                    ) as target:
                        self.store.db.backup(target)
                    copies = sorted((ROOT / "backups").glob("companion-*.sqlite3"))
                    for old in copies[:-7]:
                        old.unlink()
                    next_backup = time.time() + 86400
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.set("last_error", type(exc).__name__)
                logger.warning(
                    f"QQ companion scheduled check interrupted: {type(exc).__name__}"
                )
            await asyncio.sleep(30)

    async def recover_offline(self):
        probe = self.store.get("offline_probe_requested", False)
        self.store.set("offline_probe_requested", False)
        try:
            await self.offline.run(read_only=bool(probe))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.set("offline_recovery_error", type(exc).__name__)

    async def check_friend_space(self):
        try:
            await self.social.daily_qzone()
            self.store.set("qzone_scan_error", "")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.set("qzone_scan_error", type(exc).__name__)

    async def check_own_space(self):
        probe = self.store.get("own_space_probe_requested", False)
        self.store.set("own_space_probe_requested", False)
        try:
            await self.own_space.run(read_only=bool(probe), scheduled=not probe)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.store.set("own_space_error", type(exc).__name__)

    async def terminate(self):
        if getattr(self, 'vpet', None):
            await self.vpet.close()
        if self.reply_recovery_task:
            self.reply_recovery_task.cancel()
            await asyncio.gather(self.reply_recovery_task, return_exceptions=True)
        await self.groups.close()
        tasks = (
            list(self.workers.values())
            + ([self.loop_task] if self.loop_task else [])
            + ([self.offline_task] if self.offline_task else [])
            + ([self.own_space_task] if self.own_space_task else [])
            + ([self.qzone_task] if self.qzone_task else [])
            + list(self.memory_tasks.values())
            + ([self.activity_task] if self.activity_task else [])
            + ([self.followup_task] if self.followup_task else [])
            + ([self.expression_task] if self.expression_task else [])
            + ([self.singing_task] if self.singing_task else [])
        )
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.singing.close()
        if getattr(getattr(self, "engine", None), "voice", None):
            self.engine.voice.close()
        await self.social.close()
        self.store.db.close()
