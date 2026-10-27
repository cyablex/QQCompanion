"""Read existing private diary evidence and shared projects on a local calendar."""

import re
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from creative_projects import CreativeProjects

TZ = timezone(timedelta(hours=8))


class ExperienceCalendar:
    def __init__(self, db, cfg, root):
        self.db, self.cfg, self.root = db, cfg, root

    def month(self, month, query=''):
        if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError('请选择有效月份。')
        try:
            year, number = map(int, month.split('-'))
            start = datetime(year, number, 1, tzinfo=TZ)
            end = datetime(year + (number == 12), number % 12 + 1, 1, tzinfo=TZ)
        except (ValueError, OverflowError):
            raise ValueError('请选择有效月份。') from None
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('搜索内容最多200字。')
        query = query.strip().casefold()
        events = []

        def add(ident, kind, title, text, stamp, **extra):
            if stamp is None or not start.timestamp() <= float(stamp) < end.timestamp():
                return
            if query and query not in (title + '\n' + text).casefold():
                return
            events.append(dict(id=ident, kind=kind, title=title, text=text,
                               created=stamp, date=datetime.fromtimestamp(stamp, TZ).strftime('%Y-%m-%d'), **extra))

        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cm_items'").fetchone():
            rows = self.db.execute(
                "SELECT id,kind,key,text,evidence,source_id,source_role,created FROM cm_items "
                "WHERE contact=? AND kind IN ('journal','diary','episode') AND status='current' AND created>=? AND created<? "
                "ORDER BY created,id", (self.cfg['owner_qq'], start.timestamp(), end.timestamp()))
            for row in rows:
                # Render original evidence, never turn an old generated summary into a new fact.
                source = self.db.execute('SELECT role,text FROM messages WHERE id=? AND contact=?',
                                         (row['source_id'], self.cfg['owner_qq'])).fetchone()
                if not source or not row['evidence'] or row['evidence'] not in source['text']:
                    continue
                if row['kind'] == 'journal':
                    metadata = self.db.execute('SELECT value FROM state WHERE key=?',
                        (f"journal_sources:{self.cfg['owner_qq']}:{row['key']}",)).fetchone()
                    if not metadata:
                        continue
                    metadata = json.loads(metadata[0])
                    ids = metadata.get('ids', [])
                    if not ids or any(not self.db.execute('SELECT 1 FROM messages WHERE contact=? AND id=?',
                        (self.cfg['owner_qq'], ident)).fetchone() for ident in ids):
                        continue
                    add('journal:' + str(row['id']), 'diary', metadata.get('title', '这一天'), row['text'], row['created'],
                        speaker='大肥鱼', authored=True)
                    continue
                if row['kind'] == 'diary' and self.db.execute("SELECT 1 FROM cm_items WHERE contact=? AND kind='journal' AND key=? AND status='current'",
                    (self.cfg['owner_qq'], datetime.fromtimestamp(row['created'], TZ).strftime('%Y-%m-%d'))).fetchone():
                    continue
                title = '相处日记' if row['kind'] == 'diary' else '相处片段'
                add('memory:' + str(row['id']), row['kind'], title, row['evidence'], row['created'],
                    speaker='你' if source['role'] == 'user' else '大肥鱼', source_id=row['source_id'])

        for row in self.db.execute(
            "SELECT s.value,m.id,m.text,m.created FROM state s JOIN messages m ON m.id=json_extract(s.value,'$.message_id') "
            "WHERE s.key LIKE ? AND m.contact=? AND m.created>=? AND m.created<?",
            (f"vpet_scene:{self.cfg['bot_qq']}:{self.cfg['owner_qq']}:%", self.cfg['owner_qq'], start.timestamp(), end.timestamp())):
            add('scene:' + str(row['id']), 'scene', '喂白米饭', row['text'], row['created'],
                speaker='桌面互动', source_id=row['id'])

        for project in CreativeProjects(self.db, self.cfg, self.root).list():
            shared = dict(project_id=project['id'], project_title=project['title'])
            add('project:' + project['id'], 'project', project['title'], '开始留下这件共同的事。', project['created'], **shared)
            for entry in project['entries']:
                add('note:' + entry['id'], 'note', project['title'], entry['text'], entry['created'],
                    speaker='大肥鱼' if entry['author'] == 'companion' else '你', **shared)
            for asset in project['assets']:
                add('asset:' + project['id'] + ':' + asset['id'], 'asset', asset['title'],
                    '把歌曲放进项目。' if asset['kind'] == 'song' else '把画作放进项目。', asset.get('created'), **shared)
            for link in project.get('links', []):
                add('link:' + link['id'], 'link', link['title'], link.get('note', ''), link['created'],
                    link_id=link['id'], **shared)
        events.sort(key=lambda event: (event['created'], event['id']), reverse=True)
        return dict(month=month, timezone='Asia/Shanghai', events=events,
                    days=dict(Counter(event['date'] for event in events)))
