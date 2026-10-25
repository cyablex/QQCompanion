"""Recover private chat and explicit comment approvals; never replay admin commands."""

import asyncio
import hashlib
import time

from media import MediaPipeline
from settings import read_settings


def source_key(bot, contact, raw):
    sender = str((raw.get("sender") or {}).get("user_id", raw.get("user_id", "")))
    sequence = raw.get("real_seq")
    identity = (
        f"seq:{sequence}:at:{raw.get('time', '')}"
        if sequence and str(sequence) != "0"
        else "id:" + str(raw.get("message_id", ""))
    )
    return f"qq_source:{bot}:{contact}:{sender}:{identity}"


def message_text(raw):
    segments = raw.get("message")
    if isinstance(segments, list):
        text = "".join(
            str(segment.get("data", {}).get("text", ""))
            for segment in segments
            if isinstance(segment, dict) and segment.get("type") == "text"
        )
    elif isinstance(segments, str) and "[CQ:" not in segments:
        text = segments
    else:
        text = ""
    return MediaPipeline.describe_input(raw, text.strip())


class OfflineRecovery:
    def __init__(self, plugin):
        self.plugin, self.store = plugin, plugin.store
        self.lock = asyncio.Lock()

    async def call(self, action, **args):
        await self.plugin.check_account()
        return await asyncio.wait_for(self.plugin.bot.call_action(action, **args), 20)

    async def approval_history(self, contact, messages, floor):
        # Page back to the outstanding draft, rather than losing approvals when
        # more than 100 messages arrived while the companion was offline.
        known = {source_key("", contact, m) for m in messages}
        anchors = set()
        while messages and min(m["time"] for m in messages) > floor - 1:
            oldest = min(messages, key=lambda m: (m["time"], int(m.get("real_seq") or 0)))
            anchor = str(oldest["message_id"])
            if anchor in anchors:
                break
            anchors.add(anchor)
            response = await self.call("get_friend_msg_history", user_id=contact,
                                       message_seq=anchor, count=100, reverse_order=False, parse_mult_msg=False)
            page = response.get("messages", []) if isinstance(response, dict) else []
            added = []
            for raw in page:
                if not isinstance(raw, dict) or raw.get("message_id") is None or not isinstance(raw.get("time"), (int, float)):
                    continue
                key = source_key("", contact, raw)
                if key not in known:
                    known.add(key)
                    added.append(raw)
            if not added:
                break
            messages.extend(added)
        return messages

    async def run(self, read_only=False):
        async with self.lock:
            cfg = read_settings()
            comments = getattr(self.plugin.social, "comments", None)
            approval_floor = comments.needs_history() if comments is not None else None
            report = dict(
                updated=time.time(),
                scanned=0,
                imported=0,
                queued=0,
                errors=0,
                read_only=read_only,
                candidates=0,
            )
            if (
                not cfg["live"]
                or cfg["paused"]
                or (not read_only and not cfg.get("offline_recovery", False) and approval_floor is None)
            ):
                return report
            await self.plugin.social.reconcile_friends()
            known = set(self.store.get("known_friend_ids", [])) | {cfg["owner_qq"]}
            recent = await self.call(
                "get_recent_contact", count=min(1000, max(100, len(known) + 20))
            )
            if not isinstance(recent, list):
                raise ValueError("Unexpected recent-contact response")
            report["recent_response"] = "list"
            contacts = {cfg["owner_qq"]: None}
            for row in recent:
                if not isinstance(row, dict) or str(row.get("chatType")) != "1":
                    continue
                contact = str(row.get("peerUin", ""))
                if contact in known and contact != cfg["bot_qq"]:
                    contacts[contact] = row.get("lastestMsg")
            for contact, latest in contacts.items():
                fresh = read_settings()
                if fresh["revision"] != cfg[
                    "revision"
                ] or not self.plugin.engine.allowed(fresh, contact):
                    continue
                recover_approvals = contact == cfg["owner_qq"] and approval_floor is not None
                if contact in self.plugin.workers and not recover_approvals:
                    continue
                uncertain_since = self.store.db.execute(
                    "SELECT COALESCE(MAX(created),0) FROM actions WHERE contact=? AND state='unknown' AND kind IN ('reply','proactive','friend_proactive','error_notice')",
                    (contact,),
                ).fetchone()[0]
                cursor_key = f"offline_cursor:{cfg['bot_qq']}:{contact}"
                if isinstance(latest, dict) and latest.get("message_id") is not None:
                    if self.store.get(cursor_key) == source_key(
                        cfg["bot_qq"], contact, latest
                    ) and not recover_approvals:
                        continue
                before = self.store.last_incoming(contact)
                try:
                    response = await self.call(
                        "get_friend_msg_history",
                        user_id=contact,
                        count=100,
                        parse_mult_msg=False,
                    )
                    messages = (
                        response.get("messages") if isinstance(response, dict) else None
                    )
                    if not isinstance(messages, list):
                        raise ValueError("Unexpected history response")
                    messages = [
                        m
                        for m in messages
                        if isinstance(m, dict)
                        and m.get("message_id") is not None
                        and isinstance(m.get("time"), (int, float))
                    ]
                    if recover_approvals:
                        messages = await self.approval_history(contact, messages, approval_floor)
                    # Python's stable sort preserves native ordering within one second.
                    messages.sort(
                        key=lambda m: (float(m["time"]), int(m.get("real_seq") or 0))
                    )
                    report["scanned"] += 1
                    if not messages:
                        continue
                    report["history_fields"] = sorted(messages[-1])
                    report["array_messages"] = isinstance(
                        messages[-1].get("message"), list
                    )
                    fresh = read_settings()
                    if not read_only and recover_approvals and fresh["revision"] == cfg["revision"] and fresh["live"] and not fresh["paused"]:
                        # Approval recovery is independent of ordinary chat's
                        # last-reply boundary: an earlier AI acknowledgment may
                        # have answered the words without performing the action.
                        await comments.history(messages)
                        await comments.resume()
                    last_out = -1
                    for index, msg in enumerate(messages):
                        sender = str(
                            (msg.get("sender") or {}).get(
                                "user_id", msg.get("user_id", "")
                            )
                        )
                        if sender == cfg["bot_qq"]:
                            last_out = index
                    candidates = []
                    for msg in messages[last_out + 1 :]:
                        sender = str(
                            (msg.get("sender") or {}).get(
                                "user_id", msg.get("user_id", "")
                            )
                        )
                        if (
                            sender != contact
                            or msg["time"] <= uncertain_since
                            or not max(
                                time.time() - 86400,
                                self.store.get(f"offline_ignore_before:{contact}", 0),
                                self.store.get("migration_restored_at", 0),
                            )
                            <= msg["time"]
                            <= time.time() + 60
                        ):
                            continue
                        text, attachments = message_text(msg)
                        if (
                            (not text and not attachments)
                            or (contact == cfg["owner_qq"] and comments is not None and comments.command(text))
                            or text.startswith("/")
                            or text in {"同意", "拒绝"}
                        ):
                            continue
                        candidates.append((msg, text, attachments))
                    report["candidates"] += len(candidates)
                    if read_only:
                        continue
                    fresh = read_settings()
                    if (
                        fresh["revision"] != cfg["revision"]
                        or not self.plugin.engine.allowed(fresh, contact)
                        or not fresh.get("offline_recovery", False)
                        or contact in self.plugin.workers
                        or self.store.last_incoming(contact) != before
                    ):
                        continue
                    self._import(cfg, contact, candidates[-20:], report)
                    self.store.set(
                        cursor_key, source_key(cfg["bot_qq"], contact, messages[-1])
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    report["errors"] += 1
                    report["last_error"] = type(exc).__name__
                await asyncio.sleep(0.1)
            self.store.set("offline_recovery_report", report)
            return report

    def _import(self, cfg, contact, candidates, report):
        if not candidates:
            return
        last_source = source_key(cfg["bot_qq"], contact, candidates[-1][0])
        scheduled_key = (
            "offline_scheduled:" + hashlib.sha256(last_source.encode()).hexdigest()
        )
        if self.store.get(scheduled_key):
            return
        # A pending live task owns its retries; reconciliation must not reset them.
        if self.store.get(f"reply_job:{contact}"):
            return
        self.store.db.execute("BEGIN IMMEDIATE")
        imported = 0
        try:
            for index, (msg, text, attachments) in enumerate(candidates):
                key = source_key(cfg["bot_qq"], contact, msg)
                eid = (
                    self.store.get(key)
                    or f"{cfg['bot_qq']}:{contact}:{msg['message_id']}"
                )
                incoming = text or (
                    "[收到语音，等待识别]"
                    if attachments[0][0] == "voice"
                    else "[收到图片或表情包，等待理解]"
                )
                # Preserve the original time so old history cannot supersede new live messages.
                if self.store.incoming(
                    eid, contact, incoming[:6000], now=msg["time"] + index / 1000
                ):
                    imported += 1
                    self.store.set("event_source:" + eid, dict(time=msg["time"]))
                    if attachments:
                        self.plugin.media.capture(
                            eid, contact, text[:6000], attachments
                        )
                self.store.set(key, eid)
            last_in = self.store.last_incoming(contact)
            reply_key = f"reply:{cfg['bot_qq']}:{contact}:{last_in}"
            action = self.store.db.execute(
                "SELECT state FROM actions WHERE id=?", (reply_key,)
            ).fetchone()
            self.store.set(scheduled_key, True)
            enqueue = not action or action["state"] == "failed_not_sent"
            if enqueue:
                event = self.store.db.execute(
                    "SELECT id FROM events WHERE contact=? ORDER BY created DESC LIMIT 1",
                    (contact,),
                ).fetchone()[0]
                self.plugin.reply_queue.enqueue(contact, event)
                self.store.set(
                    f"offline_context:{contact}",
                    dict(
                        event=event,
                        source_time=candidates[-1][0]["time"],
                        recovered_at=time.time(),
                    ),
                )
            self.store.db.execute("COMMIT")
        except BaseException:
            self.store.db.execute("ROLLBACK")
            raise
        report["imported"] += imported
        if enqueue:
            self.plugin.pending[contact] = event
            self.plugin.workers[contact] = asyncio.create_task(
                self.plugin.drain(contact)
            )
            report["queued"] += 1
