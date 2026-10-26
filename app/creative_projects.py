"""Owner-scoped shared projects referencing the existing songs and drawings."""

import json
import time
import uuid

from art_gallery import ArtGallery
from settings import ROOT
from song_library import SongLibrary


class CreativeProjects:
    def __init__(self, db, cfg, root=ROOT):
        self.db, self.cfg, self.root = db, cfg, root
        self.prefix = f"creative:{cfg['bot_qq']}:{cfg['owner_qq']}:"
        self.active_key = f"creative_active:{cfg['bot_qq']}:{cfg['owner_qq']}"

    def get(self, ident):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (self.prefix + str(ident),)).fetchone()
        if not row:
            raise ValueError("没有找到这个共同创作项目。")
        return json.loads(row[0])

    def _write(self, project):
        project['updated'] = time.time()
        self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (self.prefix + project['id'], json.dumps(project, ensure_ascii=False)))
        return project

    def list(self):
        return sorted([json.loads(r[0]) for r in self.db.execute("SELECT value FROM state WHERE key LIKE ?", (self.prefix + '%',))],
                      key=lambda p: p['updated'], reverse=True)

    def create(self, title, kind='song', ident=None):
        title = str(title).strip()
        if not title or len(title) > 80 or kind not in {'song', 'comic', 'reading', 'other'}:
            raise ValueError("请填写80字以内的项目名称，并选择项目类型。")
        ident = ident or uuid.uuid4().hex
        try:
            return self.get(ident)
        except ValueError:
            pass
        project = dict(id=ident, title=title, kind=kind, stage='idea', next_step='',
                       entries=[], assets=[], created=time.time(), updated=time.time())
        self._write(project)
        self.activate(ident)
        return project

    def activate(self, ident):
        project = self.get(ident)
        self.db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (self.active_key, json.dumps(str(ident))))
        return project

    def active(self):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (self.active_key,)).fetchone()
        if not row:
            return None
        try:
            return self.get(json.loads(row[0]))
        except ValueError:
            return None

    def update(self, ident, stage, next_step):
        if stage not in {'idea', 'preview', 'adjust', 'finished'} or not isinstance(next_step, str) or len(next_step) > 500:
            raise ValueError("项目阶段或下一步内容无效。")
        self.db.execute('BEGIN IMMEDIATE')
        try:
            project = self.get(ident)
            project.update(stage=stage, next_step=next_step.strip())
            self._write(project)
            self.db.execute('COMMIT')
            return project
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def note(self, ident, text, author='user', entry_id=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 1500 or author not in {'user', 'companion'}:
            raise ValueError("请写下1500字以内的感受或想法。")
        self.db.execute('BEGIN IMMEDIATE')
        try:
            project = self.get(ident)
            entry_id = entry_id or uuid.uuid4().hex
            if not any(e['id'] == entry_id for e in project['entries']):
                project['entries'].append(dict(id=entry_id, text=text.strip(), author=author, created=time.time()))
                self._write(project)
            self.db.execute('COMMIT')
            return project
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def available_assets(self):
        songs = [dict(id=s['id'], kind='song', title=s['title'], subtitle=s['artist'], state=s['state'], scope='')
                 for s in SongLibrary(self.db, self.root).list()]
        drawings = [dict(id=d['id'], kind='drawing', title=d['title'], subtitle=d.get('caption', ''), state='ready', scope=d['scope'])
                    for d in ArtGallery(self.db, self.cfg['bot_qq'], self.root).entries()
                    if d['scope'] in {'public', self.cfg['owner_qq']}]
        return songs + drawings

    def attach(self, ident, kind, asset_id, scope=''):
        asset = next((a for a in self.available_assets() if a['kind'] == kind and a['id'] == asset_id and a['scope'] == scope), None)
        if asset is None:
            raise ValueError("这件作品不在当前可使用的曲库或画册中。")
        self.db.execute('BEGIN IMMEDIATE')
        try:
            project = self.get(ident)
            if not any(a['kind'] == kind and a['id'] == asset_id and a['scope'] == scope for a in project['assets']):
                project['assets'].append(dict(kind=kind, id=asset_id, scope=scope, title=asset['title'], created=time.time()))
                self._write(project)
            self.db.execute('COMMIT')
            return project
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def view(self, project):
        available = {(a['kind'], a['id'], a['scope']): a for a in self.available_assets()}
        return dict(project, assets=[dict(a, state=available.get((a['kind'], a['id'], a['scope']), {}).get('state', 'unavailable'))
                                     for a in project['assets']])

    def context(self, contact):
        if contact != self.cfg['owner_qq']:
            return ''
        project = self.active()
        if not project:
            return ''
        view = self.view(project)
        view['entries'] = view['entries'][-8:]
        return ('\n【你和主人正在一起做的项目，仅限当前私聊】\n'
                '这是私有的真实保存记录。根据阶段、作品和下一步自然接续；可以提出自己的偏好或具体下一步，'
                '不声称听过未试听的音频、看过未提供的画面，不把想法当作已完成作品。'
                '保存新想法或修改项目时使用creative动作，普通聊天不必自动存档。\n'
                + json.dumps(view, ensure_ascii=False))
