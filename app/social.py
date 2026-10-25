"""Opt-in friend acceptance and owner-only Qzone comments."""

import asyncio
import hashlib
import time
from datetime import datetime

from settings import read_settings
from store import TZ
from qzone_workflow import QzoneWorkflow
from comment_approvals import CommentApprovals


class Social(QzoneWorkflow):
    def __init__(self, plugin):
        self.plugin = plugin
        self.store = plugin.store
        self.qzone_api = None
        self.approval_lock = asyncio.Lock()
        self.comments = CommentApprovals(self)

    def config(self):
        return read_settings()

    async def reconcile_friends(self):
        """Read relationships after an uncertain approval; never repeat a write."""
        cfg = read_settings()
        await getattr(self.plugin, "check_messaging", self.plugin.check_account)()
        friends = await asyncio.wait_for(
            self.plugin.bot.call_action("get_friend_list"), 12
        )
        if not isinstance(friends, list):
            return
        known = {str(item.get("user_id")) for item in friends}
        self.store.set("known_friend_ids", sorted(known))
        test_ids = cfg.get("guest_ids", [])
        self.store.set(
            "friend_probe",
            {
                "checked": time.time(),
                "test_count": len(test_ids),
                "test_connected": sum(uid in known for uid in test_ids),
            },
        )
        rows = self.store.db.execute(
            "SELECT * FROM friends WHERE state='unknown'"
        ).fetchall()
        for row in rows:
            if row["user_id"] in known:
                self.store.db.execute(
                    "UPDATE friends SET state='accepted' WHERE id=?", (row["id"],)
                )
                key = "friend:" + hashlib.sha256(row["id"].encode()).hexdigest()
                self.store.outcome(
                    key, "succeeded", "relationship confirmed by later readback"
                )

    async def friends(self):
        cfg = read_settings()
        if not cfg["live"] or cfg["paused"] or not cfg["friend_accept"]:
            return
        start = (
            datetime.now(TZ)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
        )
        limit = cfg.get("friend_daily_limit", 0)
        if limit and self.store.action_count("friend_accept", start) >= limit:
            return
        row = self.store.db.execute(
            "SELECT * FROM friends WHERE state='pending' AND created>? ORDER BY created LIMIT 1",
            (time.time() - 86400,),
        ).fetchone()
        if row is None or row["user_id"] == cfg["bot_qq"]:
            return
        if cfg.get("guest_mode", "allowlist") == "allowlist" and row[
            "user_id"
        ] not in cfg.get("guest_ids", []):
            self.store.db.execute(
                "UPDATE friends SET state='needs_owner' WHERE id=?", (row["id"],)
            )
            return
        phrase = cfg.get("friend_phrase", "")
        if phrase and phrase not in row["comment"]:
            self.store.db.execute(
                "UPDATE friends SET state='needs_owner' WHERE id=?", (row["id"],)
            )
            return
        await self.plugin.check_account()
        key = "friend:" + hashlib.sha256(row["id"].encode()).hexdigest()
        if not self.store.action(key, row["user_id"], "friend_accept", []):
            return
        current = read_settings()
        if (
            current["revision"] != cfg["revision"]
            or current["paused"]
            or not current["friend_accept"]
        ):
            self.store.outcome(key, "cancelled")
            return
        self.store.outcome(key, "sending")
        self.store.db.execute(
            "UPDATE friends SET state='processing' WHERE id=?", (row["id"],)
        )
        try:
            await asyncio.wait_for(
                self.plugin.bot.call_action(
                    "set_friend_add_request", flag=row["flag"], approve=True
                ),
                30,
            )
            # QQ may report the new relationship a moment after approval.
            await asyncio.sleep(2)
            friends = await asyncio.wait_for(
                self.plugin.bot.call_action("get_friend_list"), 12
            )
            if not isinstance(friends, list) or not any(
                str(x.get("user_id")) == row["user_id"] for x in friends
            ):
                raise RuntimeError("Friend relationship not confirmed")
            self.store.outcome(key, "succeeded")
            self.store.db.execute(
                "UPDATE friends SET state='accepted' WHERE id=?", (row["id"],)
            )
            known = set(self.store.get("known_friend_ids", []))
            known.add(row["user_id"])
            self.store.set("known_friend_ids", sorted(known))
        except BaseException as exc:
            self.store.set("friend_error", type(exc).__name__)
            self.store.outcome(key, "unknown")
            self.store.db.execute(
                "UPDATE friends SET state='unknown' WHERE id=?", (row["id"],)
            )
            raise

    async def close(self):
        if self.qzone_api is not None:
            await self.qzone_api.close()
