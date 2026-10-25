"""Public activity archive and interest lines; no private conversations enter it."""

import json
import time

from conversation_memory import _score, _tokens
from reading_sources import CATEGORIES, article_url


class PublicLibrary:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS public_activities(
            bot TEXT,id TEXT,day TEXT,title TEXT,content TEXT,category TEXT,record TEXT,
            PRIMARY KEY(bot,id));
        CREATE TABLE IF NOT EXISTS public_interests(
            id INTEGER PRIMARY KEY,bot TEXT,topic TEXT,question TEXT,category TEXT,
            status TEXT,source_id TEXT,updated REAL,UNIQUE(bot,topic));
        """)

    @staticmethod
    def eligible(record):
        return record.get("status") == "completed" and record.get("public_safe") is True and record.get("origin") == "independent_public_activity"

    def save(self, cfg, record, selected_interest=None, interest_status="active", resolution=""):
        if not self.eligible(record):
            return
        keys = ("id", "day", "kind", "title", "content", "next_step", "parent_id", "version", "source", "status", "public_safe", "origin", "interest_topic", "open_question")
        saved = {key: record[key] for key in keys if key in record}
        source = dict(saved.get("source") or {})
        category = source.get("category", "")
        if not category:
            category = next((row["id"] for row in CATEGORIES if article_url(source.get("url", ""), row)), "")
        source["category"] = category
        saved["source"] = source
        self.db.execute("INSERT OR REPLACE INTO public_activities VALUES(?,?,?,?,?,?,?)", (cfg["bot_qq"], saved["id"], saved["day"], saved["title"], saved["content"], category, json.dumps(saved, ensure_ascii=False)))
        topic = str(record.get("interest_topic") or record["title"])[:80]
        question = str(record.get("open_question") or record.get("next_step") or "")[:220]
        old = self.interest(cfg, selected_interest) if selected_interest is not None else None
        if old:
            topic = old["topic"]
        if not question and not old:
            return
        status = "resolved" if old and interest_status == "resolved" and resolution and resolution in record["content"] else "paused" if interest_status == "paused" else "active"
        self.db.execute(
            "INSERT INTO public_interests(bot,topic,question,category,status,source_id,updated) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(bot,topic) DO UPDATE SET question=excluded.question,category=excluded.category,status=excluded.status,source_id=excluded.source_id,updated=excluded.updated",
            (cfg["bot_qq"], topic, question or old["question"], category, status, saved["id"], time.time()),
        )

    def import_legacy(self, cfg, records):
        key = "public_library_imported:" + cfg["bot_qq"]
        if not self.store.get(key):
            for record in records:
                self.save(cfg, record)
            self.store.set(key, True)

    def records(self, cfg, limit=200):
        return [json.loads(row[0]) for row in self.db.execute("SELECT record FROM public_activities WHERE bot=? ORDER BY day DESC,id DESC LIMIT ?", (cfg["bot_qq"], limit))][::-1]

    def recall(self, cfg, query, limit=3):
        tokens = _tokens(query)
        if not tokens:
            return []
        conditions = " OR ".join("(title LIKE ? OR content LIKE ?)" for _ in tokens)
        args = [cfg["bot_qq"]]
        for token in tokens:
            args.extend(["%" + token + "%"] * 2)
        rows = [dict(row) for row in self.db.execute("SELECT title,content,record FROM public_activities WHERE bot=? AND (" + conditions + ") ORDER BY day DESC LIMIT 80", args)]
        rows.sort(key=lambda row: _score(row["title"], tokens) * 3 + _score(row["content"], tokens), reverse=True)
        return [json.loads(row["record"]) for row in rows[:limit]]

    def interest(self, cfg, ident):
        if type(ident) is not int:
            return None
        row = self.db.execute("SELECT * FROM public_interests WHERE bot=? AND id=?", (cfg["bot_qq"], ident)).fetchone()
        return dict(row) if row else None

    def pause(self, cfg, ident):
        if type(ident) is int:
            self.db.execute("UPDATE public_interests SET status='paused',updated=? WHERE bot=? AND id=?", (time.time(), cfg["bot_qq"], ident))

    def interests(self, cfg, limit=8):
        return [dict(row) for row in self.db.execute("SELECT id,topic,question,category,status,source_id FROM public_interests WHERE bot=? AND status!='resolved' ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,updated DESC LIMIT ?", (cfg["bot_qq"], limit))]

    def preferences(self, cfg):
        return {row[0]: row[1] for row in self.db.execute("SELECT category,COUNT(*) FROM public_activities WHERE bot=? AND category!='' AND json_extract(record,'$.kind')='reading_note' GROUP BY category", (cfg["bot_qq"],))}
