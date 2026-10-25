"""Up to two spaced Qzone posts each day, based only on public material."""

import asyncio
import json
import random
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from engine import quiet
from public_images import PublicImages
from public_novelty import history_context, is_novel, recent_public
from settings import read_settings
from store import TZ
from owner_sharing import OwnerSharing


class AutoPosts:
    def __init__(self, plugin):
        self.plugin, self.store = plugin, plugin.store
        self.images = PublicImages()
        self.owner_sharing = OwnerSharing(self.store)

    def next_slot(self, cfg, day):
        # Keep the existing first-slot key so today's old record still counts.
        first = f"qzone_post:{cfg['bot_qq']}:{day}"
        keys = [first, first + ":2"]
        rows = self.store.db.execute(
            "SELECT id,created FROM actions WHERE id IN (?,?)", keys
        ).fetchall()
        used = {row["id"] for row in rows}
        return next((key for key in keys if key not in used), None), max(
            (row["created"] for row in rows), default=0
        )

    async def run(self, force=False):
        status = await self._run(force=force)
        if status == "succeeded":
            cfg = read_settings()
            last = self.store.get("last_own_post", {})
            if last.get("verified") and last.get("tid"):
                try:
                    await self.plugin.social.like_post(SimpleNamespace(uin=cfg["bot_qq"], tid=last["tid"]))
                except Exception as exc:
                    self.store.set("qzone_self_like_error", type(exc).__name__)
        return status

    async def _run(self, force=False):
        cfg = read_settings()
        now = datetime.now(TZ)
        if (
            not cfg.get("autonomous_posts", False)
            or not cfg["live"]
            or cfg["paused"]
            or quiet(now)
        ):
            return "disabled_or_quiet"
        day = now.strftime("%Y-%m-%d")
        key, last_post = self.next_slot(cfg, day)
        if key is None:
            return "already_processed_today"
        if not force and last_post and time.time() - last_post < 2 * 3600:
            return "waiting"
        sketchbook = getattr(self.plugin.engine, "sketchbook", None)
        drawing = sketchbook.pending_public(cfg) if sketchbook and cfg.get("autonomous_drawing") else None
        attempt_key = f"qzone_post_attempt:{cfg['bot_qq']}:{day}"
        if key.endswith(":2"):
            attempt_key += ":2"
        attempts = self.store.get(attempt_key, dict(count=0, last=0))
        if not force and (not drawing or attempts.get("drawing_id") == drawing["id"]) and (
            attempts["count"] >= 1 or time.time() - attempts["last"] < 3 * 3600
        ):
            return "waiting"
        self.store.set(attempt_key, dict(count=attempts["count"] + 1, last=time.time(),
                                        drawing_id=drawing["id"] if drawing else None))
        previous = [
            "\n".join(json.loads(row[0]))
            for row in self.store.db.execute(
                "SELECT content FROM actions WHERE kind='qzone_post' AND contact=? ORDER BY created DESC LIMIT 7",
                (f"qzone:{cfg['bot_qq']}",),
            )
        ]
        topics = [
            "一幅自己随兴想画的小画",
            "一个轻松、舒服、让自己开心的想象画面",
            "一句确实想留下的小想法或会心一笑的小趣味",
            "一段不用给出结论的虚拟小鱼日常心情",
        ]
        public_history = recent_public(self.store, cfg, exclude_drawing=drawing["id"] if drawing else None)
        if drawing and not await is_novel(self.plugin.engine, cfg, dict(kind="drawing", title=drawing["title"], text=drawing["caption"], composition=drawing.get("composition", "")), public_history):
            sketchbook.skip_public(cfg, drawing["id"])
            return "duplicate_content"
        task = (
            "你有机会给自己的 QQ 空间发一条独立说说。候选方向："
            + random.choice(topics)
            + "。这是可选机会，可以连续多天不发。阅读主要是自己的兴趣，不把每个小知识和研究进度都变成动态；"
            "有记录不等于值得分享，别把未完成的构思说成已经做过。不发布研究待办、草稿进度或泛泛鼓励来凑活跃。"
            "画画可以只为放松开心，画完也可以自己留着；只有自己真的想公开、读者也容易感受到趣味或心情时才reply。"
            "没有内容就skip，不必有成果或目的。最多160字。不提具体联系人、QQ号或任何未获准公开的私聊内容。"
            "只有任务单独给出的主人趣事摘要允许提‘主人’，否则不称呼主人。"
            "画里的晒太阳和伙伴是虚拟想象，不编造线下身体经历。不输出模板说明。近期已经发布的内容："
            + str(previous)
            + history_context(public_history)
        )
        if drawing:
            task += "\n可选的已完成画作（画完不代表要发布）：" + json.dumps({k: drawing.get(k, "") for k in ("id", "title", "caption", "composition")}, ensure_ascii=False)
            task += "\n如果确实想分享这幅画，回复JSON加share_drawing:true；也可以只发与画无关的文字，或skip。"
        owner_share = None
        if cfg.get("owner_public_sharing"):
            try:
                owner_share = await self.owner_sharing.candidate(self.plugin.engine, cfg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.set("owner_share_review_error", type(exc).__name__)
        if owner_share:
            task += (
                "\n【唯一获准公开的主人趣事摘要】" + owner_share["text"]
                + "\n主人授权该摘要公开，可以自然说‘主人’，并表达自己的感想；不添加身份、原话或其他私聊细节。"
                + ("可附这次经过检查的原图片。" if owner_share["image"] else "这条摘要没有可公开配图，不声称附图。")
                + "确实想分享时JSON加share_owner_chat:true，不同时分享画作；不感兴趣可以不用，也不必每天分享主人。"
            )
        result = await self.plugin.engine.decision(cfg["owner_qq"], "qzone_post", task, public=True)
        if not result or result["action"] != "reply" or not result["messages"]:
            return "no_content"
        owner_share = owner_share if result.get("share_owner_chat") else None
        drawing = drawing if result.get("share_drawing") and not owner_share else None
        content = "\n".join(result["messages"])[:500]
        if owner_share:
            from owner_sharing import PRIVATE
            if PRIVATE.search(content):
                return "contains_private_material"
        if any(
            "".join(content.split()) == "".join(old.split("\n\n配图来源：", 1)[0].split())
            for old in previous
        ):
            if drawing:
                sketchbook.skip_public(cfg, drawing["id"])
            return "duplicate_content"
        if not await is_novel(self.plugin.engine, cfg, dict(kind="post", text=content, title=drawing["title"] if drawing else "", composition=drawing.get("composition", "") if drawing else ""), public_history):
            if drawing:
                sketchbook.skip_public(cfg, drawing["id"])
            return "duplicate_content"
        if any(
            value and value in content for value in [cfg["owner_qq"], cfg["bot_qq"]]
        ):
            return "contains_account_id"
        image = owner_share["image"] if owner_share else dict(file=drawing["file"], source="大肥鱼自己的简笔画") if drawing else None
        if not drawing and not owner_share and random.random() < cfg.get("post_image_probability", 0.35):
            try:
                image = await asyncio.to_thread(self.images.search, content)
            except Exception as exc:
                self.store.set("public_image_search_error", type(exc).__name__)
            if image is None:
                image = await asyncio.to_thread(
                    self.images.whale_fallback, self.plugin.stickers
                )
            if image:
                content += "\n\n配图来源：" + image["source"]
        async with self.plugin.engine.send_lock:
            fresh = read_settings()
            if (
                fresh["revision"] != result["revision"]
                or not fresh["live"]
                or fresh["paused"]
                or not fresh.get("autonomous_posts", False)
                or quiet(datetime.now(TZ))
            ):
                return "cancelled"
            if owner_share and (not fresh.get("owner_public_sharing")
                                or self.plugin.engine.memory_epoch(cfg["owner_qq"]) != owner_share["generation"]):
                return "cancelled"
            current_key, last_post = self.next_slot(fresh, day)
            if current_key != key:
                return "already_processed_today" if current_key is None else "slot_claimed"
            if not force and last_post and time.time() - last_post < 2 * 3600:
                return "waiting"
            await self.plugin.check_account()
            if not self.store.action(
                key, f"qzone:{cfg['bot_qq']}", "qzone_post", [content]
            ):
                return "already_processed_today"
            self.store.outcome(key, "sending")
            if owner_share:
                self.owner_sharing.used(owner_share, key)
            if drawing:
                sketchbook.publication(cfg, drawing["id"], key)
            try:
                receipt = await asyncio.wait_for(
                    self.plugin.bot.call_action(
                        "send_qzone_msg",
                        content=content,
                        images=[Path(image["file"]).as_uri()] if image else [],
                        ugc_right=4,
                    ),
                    60,
                )
                tid = str(receipt.get("tid", "") if isinstance(receipt, dict) else "")
                if not tid:
                    raise RuntimeError("No Qzone post receipt")
                await self.plugin.social.ensure_qzone()
                from qzone_vendor.core.qzone import QzoneParser

                response = await self.plugin.social.qzone_api.get_feeds(
                    cfg["bot_qq"], num=10
                )
                posts = (
                    QzoneParser.parse_feeds(response.data.get("msglist") or [])
                    if response.ok
                    else []
                )
                verified = any(
                    str(post.tid) == tid
                    and "".join(post.text.split()) == "".join(content.split())
                    for post in posts
                )
                self.store.outcome(
                    key,
                    "succeeded" if verified else "unknown",
                    "tid="
                    + tid
                    + ("; readback" if verified else "; visibility not confirmed"),
                )
                self.store.set(
                    "last_own_post",
                    dict(day=day, tid=tid, verified=verified, with_image=bool(image)),
                )
                return "succeeded" if verified else "unknown"
            except BaseException:
                self.store.outcome(
                    key, "unknown", "post outcome uncertain; never retry this slot"
                )
                raise
