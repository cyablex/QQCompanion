"""Persistent contact isolation, quotas, and delivery receipts."""

import json
import sqlite3
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from settings import quota_unlimited
from budget_breakdown import read_budget
from song_library import SCHEMA as SONG_SCHEMA
from task_state import SCHEMA as TASK_STATE_SCHEMA

TZ = ZoneInfo("Asia/Shanghai")
CHAT_KINDS = ("reply", "proactive", "friend_proactive", "song_notice", "song_reply")


class BudgetExceeded(Exception):
    pass


class Store:
    def __init__(self, path, recover=True):
        self.db = sqlite3.connect(str(path), isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,contact TEXT,text TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,contact TEXT,role TEXT,text TEXT,created REAL);
        CREATE INDEX IF NOT EXISTS msg_contact ON messages(contact,id);
        CREATE TABLE IF NOT EXISTS memory(contact TEXT,text TEXT,source TEXT,created REAL,UNIQUE(contact,text));
        CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,contact TEXT,kind TEXT,content TEXT,
            state TEXT,created REAL,result TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY,day TEXT,month TEXT,kind TEXT,
            amount REAL,state TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS call_billing(id TEXT PRIMARY KEY,details TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS friends(id TEXT PRIMARY KEY,user_id TEXT,flag TEXT,
            comment TEXT,state TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS deliveries(action_id TEXT,bubble INTEGER,contact TEXT,kind TEXT,
            created REAL,PRIMARY KEY(action_id,bubble));
        CREATE TABLE IF NOT EXISTS qzone_approvals(id TEXT PRIMARY KEY,uin TEXT,tid TEXT,post_json TEXT,
            content TEXT,owner TEXT,bot TEXT,revision INTEGER,state TEXT,created REAL,expires REAL);
        CREATE TABLE IF NOT EXISTS qzone_authorizations(approval TEXT PRIMARY KEY,owner TEXT,bot TEXT,
            content TEXT,source TEXT,source_time REAL,state TEXT,due REAL,result TEXT);
        CREATE TABLE IF NOT EXISTS media_inputs(event_id TEXT,idx INTEGER,contact TEXT,kind TEXT,
            payload TEXT,state TEXT,summary TEXT,created REAL,PRIMARY KEY(event_id,idx));
        CREATE TABLE IF NOT EXISTS turn_tasks(
            id TEXT PRIMARY KEY,contact TEXT,requester TEXT,kind TEXT,args TEXT,state TEXT,
            source_event TEXT,source_time REAL,owner TEXT,bot TEXT,generation INTEGER,revision INTEGER,
            created REAL,updated REAL,result TEXT);
        CREATE INDEX IF NOT EXISTS turn_tasks_contact ON turn_tasks(contact,created);
        """)
        self.db.executescript(SONG_SCHEMA)
        self.db.executescript(TASK_STATE_SCHEMA)
        if recover:
            self.db.execute(
                "UPDATE actions SET state='failed_not_sent' WHERE state='planned' AND kind IN ('reply','proactive','friend_proactive','error_notice','song_notice','song_reply')"
            )
            self.db.execute("UPDATE actions SET state='unknown' WHERE state='sending'")
            self.db.execute(
                "UPDATE friends SET state='unknown' WHERE state='processing'"
            )
            # An interrupted request may already have been charged. Keep its reservation.
            self.db.execute(
                "UPDATE calls SET state='interrupted' WHERE state='reserved'"
            )
            self.db.execute(
                "UPDATE qzone_approvals SET state='unknown' WHERE state='sending'"
            )

    def get(self, key, default=None):
        r = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, key, value):
        self.db.execute(
            "INSERT OR REPLACE INTO state VALUES(?,?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def incoming(self, event_id, contact, text, now=None):
        # Two live events can share the Windows clock's timestamp. Keep the
        # freshness marker distinct so media/model work cannot send a stale reply.
        now = (
            max(time.time(), self.last_incoming(contact) + 0.000001)
            if now is None
            else now
        )
        cur = self.db.execute(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?)",
            (event_id, contact, text, now),
        )
        if cur.rowcount:
            self.message(contact, "user", text, now)
            return True
        return False

    def message(self, contact, role, text, now=None):
        self.db.execute(
            "INSERT INTO messages(contact,role,text,created) VALUES(?,?,?,?)",
            (contact, role, text, time.time() if now is None else now),
        )

    def replace_incoming(self, event_id, text):
        row = self.db.execute(
            "SELECT contact,created FROM events WHERE id=?", (event_id,)
        ).fetchone()
        if row:
            self.db.execute("UPDATE events SET text=? WHERE id=?", (text, event_id))
            self.db.execute(
                "UPDATE messages SET text=? WHERE contact=? AND role='user' AND created=?",
                (text, row["contact"], row["created"]),
            )

    def history(self, contact, limit=30, max_chars=12000):
        rows = self.db.execute(
            "SELECT role,text FROM messages WHERE contact=? ORDER BY id DESC LIMIT ?",
            (contact, limit),
        ).fetchall()
        chosen, size = [], 0
        for r in rows:
            if size + len(r["text"]) > max_chars and chosen:
                break
            text = r["text"][-max_chars:]
            chosen.append({"role": r["role"], "content": text})
            size += len(text)
        return list(reversed(chosen))

    def remember(self, contact, text, source="owner_command"):
        text = text.strip()[:400]
        if text:
            self.db.execute(
                "INSERT OR IGNORE INTO memory VALUES(?,?,?,?)",
                (contact, text, source, time.time()),
            )
            self.db.execute(
                "DELETE FROM memory WHERE contact=? AND rowid NOT IN (SELECT rowid FROM memory WHERE contact=? ORDER BY created DESC LIMIT 40)",
                (contact, contact),
            )

    def memories(self, contact):
        return [
            r[0]
            for r in self.db.execute(
                "SELECT text FROM memory WHERE contact=? ORDER BY created DESC LIMIT 20",
                (contact,),
            )
        ]

    def forget(self, contact):
        self.set(f"offline_ignore_before:{contact}", time.time())
        self.db.execute("UPDATE qzone_approvals SET state='rejected' WHERE id IN "
                        "(SELECT approval FROM qzone_authorizations WHERE owner=? AND state='queued')", (contact,))
        self.db.execute("UPDATE qzone_authorizations SET state='cancelled',result='主人已遗忘相关记录，取消尚未发送的评论。' "
                        "WHERE owner=? AND state='queued'", (contact,))
        self.set(f"offline_context:{contact}", None)
        self.db.execute("UPDATE song_requests SET state='cancelled',result='对方已遗忘相关记录。' WHERE contact=? AND state IN ('waiting','awaiting_source')", (contact,))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"song_pending:%:{contact}:%",))
        self.db.execute("DELETE FROM state WHERE key IN (SELECT 'event_source:' || id FROM events WHERE contact=?)", (contact,))
        self.db.execute("UPDATE turn_tasks SET args='{}',result='{}',state=CASE WHEN state IN ('queued','running','prepared','waiting','awaiting_source','awaiting_input') THEN 'cancelled' ELSE state END WHERE contact=?", (contact,))
        self.db.execute("DELETE FROM media_inputs WHERE contact=?", (contact,))
        self.db.execute("DELETE FROM memory WHERE contact=?", (contact,))
        self.db.execute("DELETE FROM state WHERE key IN (SELECT 'owner_share_used:' || id FROM messages WHERE contact=?)", (contact,))
        self.db.execute("DELETE FROM state WHERE key LIKE 'owner_share_image:%' AND json_extract(value,'$.owner')=?", (contact,))
        self.db.execute("DELETE FROM messages WHERE contact=?", (contact,))
        self.db.execute("DELETE FROM events WHERE contact=?", (contact,))
        self.db.execute("DELETE FROM state WHERE key=?", (f"reply_job:{contact}",))
        self.db.execute("DELETE FROM state WHERE key=?", (f"last_delivery:{contact}",))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"sketches:%:{contact}",))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"web_lookup:%:{contact}",))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"web_readings:%:{contact}",))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"creative:%:{contact}:%",))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"creative_active:%:{contact}",))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"vpet:%:{contact}:%",))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"vpet_msg:%:{contact}:%",))
        self.db.execute("DELETE FROM state WHERE key LIKE ?", (f"turn_chat:%:{contact}:%",))
        self.db.execute("DELETE FROM state WHERE key LIKE ? OR key LIKE ?", (f'song_offer:%:{contact}', f'outreach_result:%:{contact}'))
        self.db.execute("DELETE FROM state WHERE key LIKE ? OR key LIKE ?", (f'journal_sources:{contact}:%', f'journal_write:{contact}:%'))
        # Keep command deduplication tombstones, but remove private acknowledgments.
        self.db.execute("UPDATE state SET value=json_set(value,'$.confirmation','指令已处理；相关记忆已清理。') WHERE key LIKE ?",
                        (f"vpet_command:%:{contact}:%",))
        # Reset after deletion: SQLite may reuse message IDs when history is empty.
        from conversation_memory import ConversationMemory

        ConversationMemory(self).reset(contact)

    def reserve(self, cfg, kind, amount, now=None):
        dt = datetime.fromtimestamp(time.time() if now is None else now, TZ)
        day, month = dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m")
        validation = kind.startswith(("validation_", "acceptance_"))
        self.db.execute("BEGIN IMMEDIATE")
        try:
            budget = read_budget(self.db, day)
            d = (budget["validation_calls"] if validation else sum(item["calls"] for item in budget["breakdown"]),
                 budget["limit_validation"] if validation else budget["limit_today"])
            m = budget["limit_month"]
            if (validation or not quota_unlimited(cfg, dt)) and (
                d[0] >= (cfg.get("validation_daily_calls", 30) if validation else cfg["daily_calls"])
                or d[1] + amount > (cfg.get("validation_daily_cny", 0.5) if validation else cfg["daily_cny"])
                or m + amount > cfg["monthly_cny"]
            ):
                raise BudgetExceeded("Project model quota exhausted")
            rid = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?)",
                (rid, day, month, kind, amount, "reserved", dt.timestamp()),
            )
            route = "official" if kind.endswith("_official_fallback") or kind in {"web_search", "web_fetch"} else cfg.get("model_route", "official")
            self.db.execute("INSERT INTO call_billing VALUES(?,?)", (rid, json.dumps({"billing_context": {"route": route}})))
            self.db.execute("COMMIT")
            return rid
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def settle(self, rid, amount=None, state="completed", usage=None):
        if amount is None:
            self.db.execute("UPDATE calls SET state=CASE WHEN state IN ('completed','completed_with_error') AND ?='failed_or_unknown' THEN 'completed_with_error' ELSE ? END WHERE id=?", (state, state, rid))
        else:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute(
                    "UPDATE calls SET amount=?,state=? WHERE id=?",
                    (max(0, amount), state, rid),
                )
                if usage:
                    details = {key: usage[key] for key in (
                        "input_other", "input_cached", "output", "billing_rates", "billing_context"
                    ) if key in usage}
                    self.db.execute("INSERT OR REPLACE INTO call_billing VALUES(?,?)",
                                    (rid, json.dumps(details, ensure_ascii=False)))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def action(self, key, contact, kind, content):
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO actions(id,contact,kind,content,state,created) VALUES(?,?,?,?,?,?)",
            (
                key,
                contact,
                kind,
                json.dumps(content, ensure_ascii=False),
                "planned",
                time.time(),
            ),
        ).rowcount
        if inserted:
            return True
        # Only a preflight failure proves that retrying cannot duplicate a send.
        return bool(
            self.db.execute(
                "UPDATE actions SET content=?,state='planned',created=?,result='' WHERE id=? AND state='failed_not_sent'",
                (json.dumps(content, ensure_ascii=False), time.time(), key),
            ).rowcount
        )

    def outcome(self, key, state, result=""):
        self.db.execute(
            "UPDATE actions SET state=?,result=? WHERE id=?",
            (state, str(result)[:1000], key),
        )

    def delivered(self, key, bubble, contact, kind, text):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO deliveries VALUES(?,?,?,?,?)",
                (key, bubble, contact, kind, time.time()),
            ).rowcount
            if inserted:
                self.message(contact, "assistant", text)
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def sent_count(self, since, contact=None, kinds=("reply", "friend_proactive", "song_notice", "song_reply")):
        marks = ",".join("?" for _ in kinds)
        where = " AND contact=?" if contact else ""
        params = [since, *kinds] + ([contact] if contact else [])
        count = self.db.execute(
            f"SELECT COUNT(*) FROM deliveries WHERE created>=? AND kind IN ({marks}){where}",
            params,
        ).fetchone()[0]
        # Preserve the count from receipts written by versions before deliveries.
        rows = self.db.execute(
            f"SELECT content FROM actions WHERE created>=? AND kind IN ({marks}){where} AND state='succeeded' AND NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.action_id=actions.id)",
            params,
        ).fetchall()
        # Uncertain attempts may already have reached QQ. Keep their remaining
        # bubbles reserved even after a later incoming unlocks a new reply.
        uncertain = self.db.execute(
            f"SELECT content,(SELECT COUNT(*) FROM deliveries d WHERE d.action_id=actions.id) FROM actions WHERE created>=? AND kind IN ({marks}){where} AND state IN ('unknown','acknowledged_unknown')",
            params,
        ).fetchall()
        return (
            count
            + sum(len(json.loads(row[0])) for row in rows)
            + sum(max(0, len(json.loads(row[0])) - row[1]) for row in uncertain)
        )

    def last_incoming(self, contact):
        return self.db.execute(
            "SELECT COALESCE(MAX(created),0) FROM messages WHERE contact=? AND role='user'",
            (contact,),
        ).fetchone()[0]

    def last_action(self, contact, kind):
        return self.db.execute(
            "SELECT COALESCE(MAX(created),0) FROM actions WHERE contact=? AND kind=? AND state IN ('succeeded','unknown','acknowledged_unknown','sending')",
            (contact, kind),
        ).fetchone()[0]

    def action_count(self, kind, since, contact=None):
        where = " AND contact=?" if contact is not None else ""
        return self.db.execute(
            "SELECT COUNT(*) FROM actions WHERE kind=? AND created>=? AND state IN ('succeeded','unknown','acknowledged_unknown','sending')"
            + where,
            (kind, since, contact) if contact is not None else (kind, since),
        ).fetchone()[0]

    def has_unknown(self, contact):
        # Quarantine only the uncertain old action. A genuinely newer incoming
        # is a new conversational turn, not permission to retry that old action.
        # Comparing source timestamps also avoids unlocking on old offline replay.
        latest = self.last_incoming(contact)
        if latest:
            self.db.execute(
                "UPDATE actions SET state='acknowledged_unknown',result=result||'; newer incoming allows fresh reply; old action never retried' WHERE contact=? AND state='unknown' AND kind IN ('reply','proactive','friend_proactive','error_notice') AND created<?",
                (contact, latest),
            )
        return bool(
            self.db.execute(
                "SELECT 1 FROM actions WHERE contact=? AND state='unknown' AND kind IN ('reply','proactive','friend_proactive','error_notice') LIMIT 1",
                (contact,),
            ).fetchone()
        )

    def status(self):
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        r = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(amount),0) FROM calls WHERE day=? AND NOT (kind GLOB 'validation_*' OR kind GLOB 'acceptance_*')", (day,)
        ).fetchone()
        validation = self.db.execute(
            "SELECT COUNT(*),COALESCE(SUM(amount),0) FROM calls WHERE day=? AND (kind GLOB 'validation_*' OR kind GLOB 'acceptance_*')", (day,)
        ).fetchone()
        unknown = self.db.execute(
            "SELECT COUNT(*) FROM actions WHERE state='unknown'"
        ).fetchone()[0]
        budget = read_budget(self.db, day)
        return {
            "calls_today": r[0],
            "estimated_cny_today": round(budget["usage_estimate"], 4),
            "active_cny_today": budget["active_reserve"],
            "limit_cny_today": round(budget["limit_today"], 4),
            "validation_calls_today": validation[0],
            "validation_cny_today": round(budget["validation_usage_estimate"], 4),
            "all_calls_today": r[0] + validation[0],
            "all_estimated_cny_today": round(budget["usage_estimate"] + budget["validation_usage_estimate"], 4),
            "unknown_actions": unknown,
            "chat_messages_today": self.sent_count(
                datetime.now(TZ)
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .timestamp(),
                kinds=CHAT_KINDS,
            ),
            "pending_comment_approvals": self.db.execute(
                "SELECT count(*) FROM qzone_approvals WHERE state='pending' AND expires>?",
                (time.time(),),
            ).fetchone()[0],
        }
