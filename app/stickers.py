"""Curated local whale-girl emotes and native QQ favorite collection."""

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

from settings import ROOT


class StickerLibrary:
    def __init__(self):
        self.items = {}
        path = ROOT / "assets/memes/catalog.json"
        if path.exists():
            for item in json.loads(path.read_text(encoding="utf-8")):
                file = Path(item["file"])
                if (
                    file.is_file()
                    and file.resolve().is_relative_to((ROOT / "assets/memes").resolve())
                    and hashlib.sha256(file.read_bytes()).hexdigest() == item["sha256"]
                ):
                    self.items[item["id"]] = item

    def prompt(self, owner=False):
        rows = [
            f"{key}={item['tag']}（{item['meaning']}）"
            for key, item in self.items.items()
            if owner or key != "085_master"
        ]
        return (
            "可选鲸鱼娘表情："
            + "；".join(rows)
            + "。这些是你自己的鲸鱼娘表情，按具体语气自然选用，轻松聊天里应当常用，不只是偶尔装饰；开心、得意、吐槽、惊讶、疑惑、感谢、答应、告别时主动挑合适的一张。"
            "使用时在回复末尾单独加一行 [表情:对应ID]，每轮最多一个。"
            "告别、晚安、收到、感谢等场景，表情已经表达完整意思时可以只输出这个标记，不必再配一句文字。"
            "例如对方说‘我先睡啦，晚安’，你可以只回[表情:maid_sleep]；不是每次告别都固定用同一种形式。"
            "鲸鱼娘表情和QQ原生表情每轮选一种，鲸鱼娘有合适表达时优先用自己的表情；每轮最多一个，不叠加；严肃解释、争执、对方倾诉难过时先认真回应，不用嬉笑贴图。"
            "结合语境变化选择，不连续机械重复同一张，也不要只用开心这一张。捧饭、鼓腮、摸头等动画同样可以表达自己的反应，不用逐字解释画面；贴图文字不代表真的执行过某个操作。"
        )

    def extract(self, text):
        match = re.search(r"\n?\[表情:([A-Za-z0-9_-]+)\]\s*$", text)
        if not match:
            return text, None
        return text[: match.start()].strip(), match.group(1) if match.group(
            1
        ) in self.items else None

    async def collect(self, plugin):
        if not self.items:
            return
        await plugin.check_account()

        def digest_set(value):
            result = set()
            if isinstance(value, dict):
                for key, child in value.items():
                    if "md5" in key.lower() and isinstance(child, str):
                        result.add(child.lower())
                    result |= digest_set(child)
            elif isinstance(value, list):
                for child in value:
                    result |= digest_set(child)
            return result

        before = await asyncio.wait_for(
            plugin.bot.call_action("fetch_custom_face_detail", count=200), 15
        )
        known = digest_set(before)
        for item in self.items.values():
            path = Path(item["file"])
            md5 = hashlib.md5(path.read_bytes()).hexdigest()
            key = "favorite:" + md5
            if md5 in known:
                if plugin.store.db.execute(
                    "SELECT 1 FROM actions WHERE id=?", (key,)
                ).fetchone():
                    plugin.store.outcome(key, "succeeded", "favorite readback")
                continue
            if not plugin.store.action(key, "self", "favorite_emote", [item["id"]]):
                continue
            await plugin.check_account()
            plugin.store.outcome(key, "sending")
            try:
                await asyncio.wait_for(
                    plugin.bot.call_action(
                        "add_custom_face", file=str(path), is_origin=True
                    ),
                    20,
                )
                # Retain uncertainty until the favorite list confirms the exact file.
                plugin.store.outcome(key, "unknown", "awaiting favorite readback")
            except Exception as exc:
                plugin.store.outcome(key, "unknown", type(exc).__name__)
            await asyncio.sleep(0.4)
        after = await asyncio.wait_for(
            plugin.bot.call_action("fetch_custom_face_detail", count=200), 15
        )
        known = digest_set(after)
        confirmed = 0
        for item in self.items.values():
            md5 = hashlib.md5(Path(item["file"]).read_bytes()).hexdigest()
            if md5 in known:
                confirmed += 1
                plugin.store.outcome(
                    "favorite:" + md5, "succeeded", "favorite readback"
                )
        plugin.store.set(
            "emote_collection",
            dict(
                local_count=len(self.items),
                confirmed_favorites=confirmed,
                checked=time.time(),
            ),
        )
