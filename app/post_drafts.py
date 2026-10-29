"""Exact-content review of this account's own Qzone posts."""

import json
import secrets
import shutil
import time
from pathlib import Path

from settings import ROOT


SCHEMA = """
CREATE TABLE IF NOT EXISTS qzone_post_drafts(
 id TEXT PRIMARY KEY, owner TEXT, bot TEXT, content TEXT, payload TEXT,
 state TEXT, created REAL, expires REAL, result TEXT DEFAULT '');
"""


class PostDrafts:
    def __init__(self, db):
        self.db = db
        self.db.executescript(SCHEMA)

    def rows(self, cfg):
        self.db.execute("UPDATE qzone_post_drafts SET state='expired',result='草稿已过期，未发布。' "
                        "WHERE state IN ('pending','approved') AND expires<?", (time.time(),))
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM qzone_post_drafts WHERE owner=? AND bot=? ORDER BY created DESC LIMIT 12",
            (cfg['owner_qq'], cfg['bot_qq']))]

    def active(self, cfg):
        return next((r for r in self.rows(cfg) if r['state'] in {'pending', 'approved', 'sending'}), None)

    def create(self, cfg, content, payload):
        token = secrets.token_hex(4)
        payload = dict(payload)
        image = payload.get('image')
        if image:
            source = Path(image['file'])
            folder = ROOT / 'data/post-drafts' / token
            folder.mkdir(parents=True)
            target = folder / ('image' + source.suffix)
            shutil.copyfile(source, target)
            payload['image'] = dict(image, file=str(target.resolve()))
        self.db.execute('INSERT INTO qzone_post_drafts VALUES(?,?,?,?,?,?,?,?,?)',
                        (token, cfg['owner_qq'], cfg['bot_qq'], content,
                         json.dumps(payload, ensure_ascii=False), 'pending', time.time(), time.time() + 72 * 3600, '等待你确认，尚未发布。'))
        return token

    def decide(self, cfg, token, approve, content, source_time=None):
        self.rows(cfg)
        row = self.db.execute('SELECT * FROM qzone_post_drafts WHERE id=? AND owner=? AND bot=?',
                              (token, cfg['owner_qq'], cfg['bot_qq'])).fetchone()
        if not row or row['content'] != content:
            raise ValueError('草稿与当前展示不一致，请重新查看后确认。')
        if row['state'] not in ({'pending'} if approve else {'pending', 'approved'}):
            raise ValueError('这条草稿已处理或已过期，不会重复发布。')
        if source_time is not None and not row['created'] - 1 <= source_time <= min(row['expires'], time.time() + 60):
            raise ValueError('批准时间与草稿不对应，未发布。')
        state = 'approved' if approve else 'rejected'
        result = '已批准，等待后台按原文发布。' if approve else '已拒绝，不会发布。'
        changed = self.db.execute("UPDATE qzone_post_drafts SET state=?,result=? WHERE id=? AND state IN ('pending','approved')",
                                  (state, result, token)).rowcount
        if not changed:
            raise ValueError('这条草稿已处理，请刷新状态。')
        return result

    def finish(self, token, state, result):
        self.db.execute('UPDATE qzone_post_drafts SET state=?,result=? WHERE id=?', (state, result, token))
