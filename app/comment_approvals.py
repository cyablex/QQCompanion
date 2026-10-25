"""Durable owner comment approvals, shared by live messages and offline history."""

import asyncio
import json
import re
import time
from types import SimpleNamespace

from offline_recovery import message_text, source_key


BULK_APPROVAL = re.compile(
    r"(?:(这)?(两个|两条|二个|2个|2条|三个|三条|3个|3条)|全部|全|这几条)?"
    r"都(?:同意|(?:可以)?(?:发|发送|发出去)(?:吧)?)[。！!]?"
)
TOKEN_COMMAND = re.compile(
    r"(同意评论|同意发送|同意|可以发|拒绝评论|拒绝|不同意)"
    r"\s*([0-9a-fA-F]{6,8})?[。！!]?"
)
COMPOUND_APPROVAL = re.compile(
    r"((?:同意(?:评论|发送)?|可以发|拒绝(?:评论)?|不同意)\s*[0-9a-fA-F]{6,8})[，,；;]\s*(.+)"
)
DRAFTS = re.compile(
    r"空间评论待确认\s*\[([0-9a-f]{8})\][\s\S]*?拟发布的评论[：:]\s*"
    r"([\s\S]*?)(?=\s*同意请发[：:]|空间评论待确认|\Z)"
)


def shown_drafts(text):
    return {token: draft.strip() for token, draft in DRAFTS.findall(text)
            if 0 < len(draft.strip()) <= 200}


class CommentApprovals:
    def __init__(self, social):
        self.social, self.store = social, social.store

    @staticmethod
    def command(text):
        clean = text.strip().lstrip("/").strip()
        return TOKEN_COMMAND.fullmatch(clean) or BULK_APPROVAL.fullmatch(clean) or COMPOUND_APPROVAL.fullmatch(clean)

    def needs_history(self):
        cfg = self.social.config()
        return self.store.db.execute(
            "SELECT MIN(created) FROM qzone_approvals WHERE owner=? AND bot=? "
            "AND state IN ('pending','approved','expired')",
            (cfg["owner_qq"], cfg["bot_qq"]),
        ).fetchone()[0]

    def capture(self, contact, text, source, source_time, shown=None, historical=False, tokens=None, reject_selection=False):
        cfg = self.social.config()
        clean = text.strip().lstrip("/").strip()
        compound = COMPOUND_APPROVAL.fullmatch(clean)
        if compound:
            clean = compound[1]
        match, bulk = TOKEN_COMMAND.fullmatch(clean), BULK_APPROVAL.fullmatch(clean)
        if contact != cfg["owner_qq"] or (not match and not bulk):
            return None
        if not isinstance(source_time, (int, float)) or not 0 < source_time <= time.time() + 60:
            return "批准消息的时间无法核对，没有发布评论。"
        if source_time <= max(self.store.get(f"offline_ignore_before:{contact}", 0), self.store.get("migration_restored_at", 0)):
            return []
        shown = self.social.reviewed_approvals(contact) if shown is None else shown
        rows = [dict(r) for r in self.store.db.execute(
            "SELECT * FROM qzone_approvals WHERE owner=? AND bot=? AND created<=?",
            (contact, cfg["bot_qq"], source_time + 1),
        )]
        if match and not match[2] and not shown and "评论" not in match[1] and not any(
            r["state"] in {"pending", "approved"} for r in rows
        ):
            return None
        reject = bool(match and match[1].startswith(("拒绝", "不同意")))
        if tokens is not None:
            if not isinstance(tokens, list) or not tokens or len(tokens) != len(set(tokens)):
                return "审批编号不明确，没有发布。"
            rows = [r for r in rows if r["id"] in tokens]
            if len(rows) != len(tokens):
                return "审批编号与当前账号的草稿不对应，没有发布。"
            reject = bool(reject_selection)
        elif match and match[2]:
            rows = [r for r in rows if r["id"].startswith(match[2].lower())]
            if len(rows) != 1:
                return "审批编号不明确，请用 /待审批 核对；尚未发布。"
        else:
            rows = [r for r in rows if r["id"] in shown and r["state"] in {"pending", "approved", "expired"}]
            count = (2 if bulk and bulk[2] and bulk[2].startswith(("两", "二", "2"))
                     else 3 if bulk and bulk[2] else len(rows) if bulk else 1)
            if not rows or len(rows) != count:
                return "无法明确对应你刚看到的评论，请带审批编号确认；尚未发布。"
        selected = []
        for row in rows:
            token = row["id"]
            old = self.store.db.execute("SELECT * FROM qzone_authorizations WHERE approval=?", (token,)).fetchone()
            if row["state"] not in {"pending", "approved", "expired"}:
                if historical:
                    continue
                return "该评论已处理或正在核对结果，不会重复发布。"
            if old and old["state"] != "queued":
                continue
            if old and not reject:
                continue
            if source_time > row["expires"] and not reject:
                return f"[{token}] 你发出批准时草稿已过期，没有发布。"
            if not reject:
                preview = self.store.db.execute(
                    "SELECT content FROM actions WHERE id=? AND state='succeeded' AND created<=?",
                    ("approval_request:" + token, source_time + 1),
                ).fetchone()
                presented = shown.get(token)
                if presented is None and preview:
                    presented = shown_drafts("\n".join(json.loads(preview[0]))).get(token)
                if presented != row["content"]:
                    return f"[{token}] 草稿与当时展示的内容不一致，需要重新确认；尚未发布。"
            selected.append(row)
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            for row in selected:
                state = "rejected" if reject else "queued"
                result = "已拒绝，不会发布这条评论。" if reject else "已收到明确批准，等待连接恢复后发布。"
                self.store.db.execute(
                    "INSERT OR REPLACE INTO qzone_authorizations VALUES(?,?,?,?,?,?,?,?,?)",
                    (row["id"], contact, cfg["bot_qq"], row["content"], source,
                     source_time, state, 0, result),
                )
                self.store.db.execute("UPDATE qzone_approvals SET state=? WHERE id=?",
                                      ("rejected" if reject else "approved", row["id"]))
            self.store.db.execute("COMMIT")
        except BaseException:
            self.store.db.execute("ROLLBACK")
            raise
        return [r["id"] for r in selected]

    async def history(self, messages):
        cfg = self.social.config()
        floor = self.needs_history()
        if floor is None:
            return
        block = []
        notices = []
        for raw in messages:
            sender = str((raw.get("sender") or {}).get("user_id", raw.get("user_id", "")))
            text, _ = message_text(raw)
            if sender == cfg["bot_qq"]:
                block.append(text)
                continue
            if sender == cfg["owner_qq"] and raw["time"] >= floor - 1 and self.command(text):
                result = self.capture(sender, text, source_key(cfg["bot_qq"], sender, raw),
                                      raw["time"], shown_drafts("\n".join(block)), historical=True)
                if isinstance(result, str):
                    notices.append(("comment_command:" + str(raw["message_id"]), result))
                if COMPOUND_APPROVAL.fullmatch(text.strip().lstrip("/").strip()):
                    notices.append(("comment_command_tail:" + str(raw["message_id"]),
                                    "组合请求中的评论批准已单独核对；后半句改稿尚未执行，没有擅自修改其他草稿。"))
            block = []
        # Persist the whole chronological batch, including later refusals,
        # before yielding to notification or publication tasks.
        for key, text in notices:
            await self.notice(key, text)

    def finish(self, token, state, result, due=0):
        self.store.db.execute("UPDATE qzone_authorizations SET state=?,result=?,due=? WHERE approval=?",
                              (state, result, due, token))
        if state == "blocked":
            self.store.db.execute("UPDATE qzone_approvals SET state='failed' WHERE id=? AND state='approved'", (token,))

    async def notice(self, key, text):
        cfg = self.social.config()
        key = "approval_result:" + key
        prior = self.store.db.execute("SELECT state FROM actions WHERE id=?", (key,)).fetchone()
        if prior and prior[0] == "cancelled":
            self.store.outcome(key, "failed_not_sent")
        return await self.social.plugin.engine.deliver(
            cfg["owner_qq"], "approval_result",
            dict(action="reply", messages=[text], revision=cfg["revision"],
                 read_through=self.store.last_incoming(cfg["owner_qq"])), key=key,
        )

    async def resume(self):
        cfg = self.social.config()
        if not cfg["live"] or cfg["paused"] or not cfg.get("qzone"):
            return
        rows = self.store.db.execute("SELECT * FROM qzone_authorizations WHERE owner=? AND bot=? ORDER BY source_time",
                                     (cfg["owner_qq"], cfg["bot_qq"])).fetchall()
        for saved in rows:
            token = saved["approval"]
            if saved["state"] in {"queued", "unknown"} and saved["due"] <= time.time():
                async with self.social.approval_lock:
                    try:
                        await self.publish(token, cfg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        action = self.comment_action(token)
                        uncertain = action and action["state"] in {"sending", "unknown", "succeeded"}
                        state = "unknown" if uncertain else "queued"
                        self.finish(token, state,
                                    "已尝试发送，结果待核对；将继续读回确认，不重复发送。" if uncertain
                                    else "批准已保存，发布前连接或读取暂时失败，恢复后自动继续。",
                                    time.time() + 60)
                        self.store.set("comment_approval_error", type(exc).__name__)
            row = self.store.db.execute("SELECT state,result FROM qzone_authorizations WHERE approval=?", (token,)).fetchone()
            try:
                await self.notice(token + ":" + row["state"], f"[{token}] {row['result']}")
            except Exception as exc:
                self.store.set("comment_approval_notice_error", type(exc).__name__)

    def comment_action(self, token):
        return self.store.db.execute(
            "SELECT a.* FROM actions a JOIN qzone_approvals p ON a.id='qzone_comment:'||p.uin||':'||p.tid WHERE p.id=?",
            (token,),
        ).fetchone()

    async def publish(self, token, cfg):
        auth = self.store.db.execute("SELECT * FROM qzone_authorizations WHERE approval=?", (token,)).fetchone()
        if auth["state"] not in {"queued", "unknown"}:
            return
        row = self.store.db.execute("SELECT * FROM qzone_approvals WHERE id=?", (token,)).fetchone()
        if (not row or row["owner"] != cfg["owner_qq"] or row["bot"] != cfg["bot_qq"]
                or row["content"] != auth["content"] or row["state"] in {"rejected", "changed"}
                or auth["source_time"] <= max(self.store.get(f"offline_ignore_before:{cfg['owner_qq']}", 0), self.store.get("migration_restored_at", 0))
                or not row["created"] - 1 <= auth["source_time"] <= row["expires"]):
            self.finish(token, "blocked", "批准对应的草稿已改变或撤销，没有发布。")
            return
        await self.social.ensure_qzone()
        post = SimpleNamespace(**json.loads(row["post_json"]))
        detail = await self.social.qzone_api.get_detail(post)
        if not detail.ok:
            raise ConnectionError("Comment detail unavailable before send")
        from qzone_vendor.core.qzone import QzoneParser

        current = next((p for p in QzoneParser.parse_feeds([detail.data])
                        if str(p.uin) == row["uin"] and str(p.tid) == row["tid"]), None)
        if current is None or "".join(current.text.split()) != "".join(post.text.split()):
            self.store.db.execute("UPDATE qzone_approvals SET state='changed' WHERE id=?", (token,))
            self.finish(token, "blocked", "动态已修改、删除或不再可读，不能按旧稿发布。")
            return
        own = [c for c in current.comments if str(c.uin) == cfg["bot_qq"]]
        action = self.comment_action(token)
        if any(c.content.strip() == auth["content"] for c in own):
            if action:
                self.store.outcome(action["id"], "succeeded", "readback")
            self.store.db.execute("UPDATE qzone_approvals SET state='succeeded' WHERE id=?", (token,))
            self.finish(token, "succeeded", "已按你批准的原文发布，并读回确认。")
            return
        if action and action["state"] not in {"planned", "failed_not_sent"}:
            self.finish(token, "unknown", "发送结果仍待核对，将继续读回确认，不重复发送。", time.time() + 300)
            return
        if own:
            self.finish(token, "blocked", "这条动态已有本账号的其他评论，没有重复发布。")
            return
        if action and action["state"] == "planned":
            self.store.outcome(action["id"], "failed_not_sent", "interrupted before send started")
        self.store.db.execute("UPDATE qzone_approvals SET state='approved' WHERE id=?", (token,))
        status = await self.social.publish_comment(current, auth["content"], cfg, token)
        if status == "succeeded":
            self.finish(token, status, "已按你批准的原文发布，并读回确认。")
        elif status == "unknown":
            self.finish(token, status, "已尝试发送，结果待核对；将继续读回确认，不重复发送。", time.time() + 60)
        elif status in {"cancelled", "limited"}:
            self.finish(token, "queued", "批准已保存，恢复发布条件后自动继续。", time.time() + 60)
        else:
            self.finish(token, "blocked", "空间接口未接受本次评论或批准已失效，未确认发布成功。")
