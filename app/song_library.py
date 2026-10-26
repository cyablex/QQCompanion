"""A local catalogue of finished covers and their durable delivery requests."""

import json
import re
import time
import uuid
from pathlib import Path

from settings import ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs(
 id TEXT PRIMARY KEY,title TEXT,artist TEXT,title_key TEXT,artist_key TEXT,aliases TEXT,
 source_url TEXT,source_title TEXT,state TEXT,progress TEXT,created REAL,updated REAL,
 duration REAL DEFAULT 0,bytes INTEGER DEFAULT 0,file TEXT DEFAULT '',error TEXT DEFAULT '',worker_pid INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS songs_lookup ON songs(title_key,artist_key);
CREATE TABLE IF NOT EXISTS song_requests(
 id TEXT PRIMARY KEY,song_id TEXT,contact TEXT,requester TEXT,event_id TEXT,owner TEXT,bot TEXT,
 state TEXT,created REAL,updated REAL,due REAL DEFAULT 0,generation INTEGER DEFAULT 0,result TEXT DEFAULT '');
"""


def normalized(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", str(text).casefold())


def artist_key(text):
    return normalized(re.sub(r"(?:版本|版)$", "", text.strip()))


class SongLibrary:
    def __init__(self, db, root=ROOT):
        self.db, self.root = db, Path(root)

    @property
    def directory(self):
        return self.root / "data/songbook"

    def get(self, song_id):
        row = self.db.execute("SELECT * FROM songs WHERE id=?", (song_id,)).fetchone()
        return dict(row) if row else None

    def find(self, title, artist, source_url=""):
        rows = self.db.execute("SELECT * FROM songs WHERE title_key=? AND state!='deleted' ORDER BY (state='ready') DESC,created DESC",
                               (normalized(title),)).fetchall()
        key = artist_key(artist)
        for row in rows:
            if (key in {row["artist_key"], *(artist_key(a) for a in json.loads(row["aliases"]))}
                    and (not source_url or row["source_url"].rstrip("/") == source_url.rstrip("/"))):
                return dict(row)
        return None

    def add(self, title, artist, source_url="", aliases=()):
        title, artist = title.strip()[:100], artist.strip()[:100]
        if not title or not artist:
            raise ValueError("需要歌名和歌手或版本。")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.find(title, artist, source_url)
            if not old and source_url:
                previous = self.find(title, artist)
                if previous and previous["state"] in {"queued", "failed", "needs_source"}:
                    self.db.execute("UPDATE songs SET source_url=?,state='queued',error='',progress='已补充音源，等待准备',updated=? WHERE id=?",
                                    (source_url, time.time(), previous["id"]))
                    self.db.execute("UPDATE song_requests SET state='waiting',due=0 WHERE song_id=? AND state='awaiting_source'", (previous["id"],))
                    old = self.get(previous["id"])
            if old:
                self.db.execute("COMMIT")
                return old
            token, now = uuid.uuid4().hex, time.time()
            self.db.execute(
                "INSERT INTO songs(id,title,artist,title_key,artist_key,aliases,source_url,source_title,state,progress,created,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (token, title, artist, normalized(title), artist_key(artist), json.dumps(list(aliases), ensure_ascii=False),
                 source_url, "", "queued", "等待准备", now, now),
            )
            self.db.execute("COMMIT")
            return self.get(token)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def audio_path(self, song_id):
        if not re.fullmatch(r"[0-9a-f]{32}", song_id):
            raise ValueError("曲目编号无效。")
        return self.directory / "audio" / (song_id + ".mp3")

    def allowed_file(self, song_id, file):
        song = self.get(song_id)
        if not song or song["state"] != "ready":
            return False
        expected = self.audio_path(song_id).resolve()
        return Path(file).resolve() == expected and expected.is_file() and song["file"] == str(expected)

    def list(self):
        rows = [dict(r) for r in self.db.execute("SELECT id,title,artist,state,progress,created,updated,duration,bytes,error,source_url "
                                               "FROM songs WHERE state!='deleted' ORDER BY updated DESC")]
        for row in rows:
            master = self.audio_path(row['id']).with_suffix('.flac')
            row['master_bytes'] = master.stat().st_size if master.is_file() else 0
        return rows

    def delete(self, song_id):
        song = self.get(song_id)
        if not song or song["state"] == "deleted":
            return False
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("UPDATE songs SET state='deleted',updated=? WHERE id=?", (time.time(), song_id))
            self.db.execute("UPDATE song_requests SET state='cancelled',result='曲目已从曲库删除。',updated=? "
                            "WHERE song_id=? AND state IN ('waiting','ready','awaiting_source')", (time.time(), song_id))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        # Only the generated catalogue file is removed; source files are untouched.
        self.audio_path(song_id).unlink(missing_ok=True)
        self.audio_path(song_id).with_suffix('.flac').unlink(missing_ok=True)
        return True
