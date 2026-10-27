"""An illustrated reader over existing, owner-scoped diary and event evidence."""

import base64
import json
import re
import time
from collections import defaultdict
from datetime import datetime

from creative_projects import CreativeProjects
from art_gallery import ArtGallery
from experience_calendar import ExperienceCalendar
from store import TZ

THEMES = ('cover', 'quiet', 'song', 'reading', 'rice', 'night', 'memory')


class DiaryBook:
    def __init__(self, db, cfg, root):
        self.db, self.cfg, self.root = db, cfg, root
        self.owner = cfg['owner_qq']
        self.prefix = f"diary_book:{cfg['bot_qq']}:{self.owner}:"

    def _get(self, key, default=None):
        row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def catalog(self):
        months = {datetime.now(TZ).strftime('%Y-%m')}
        for row in self.db.execute('SELECT DISTINCT strftime(\'%Y-%m\',created+28800,\'unixepoch\') FROM messages WHERE contact=?', (self.owner,)):
            if row[0]:
                months.add(row[0])
        for project in CreativeProjects(self.db, self.cfg, self.root).list():
            stamps = [project['created'], *[e['created'] for e in project['entries']], *[a['created'] for a in project['assets']]]
            if project.get('completed'):
                stamps.append(project['completed'])
            months.update(datetime.fromtimestamp(stamp, TZ).strftime('%Y-%m') for stamp in stamps)
        return sorted(months, reverse=True)

    @staticmethod
    def theme(text):
        for theme, words in [('rice', ('米饭', '饭碗', '喂饭')), ('song', ('歌', '副歌', '试听', '音色')),
                             ('reading', ('读', '文章', '书', '看过')), ('night', ('晚安', '夜', '月亮', '休息'))]:
            if any(word in text for word in words):
                return theme
        return 'quiet'

    def activity(self):
        value = self._get(f"diary_activity:{self.cfg['bot_qq']}:{self.owner}", {})
        epoch = self.generation()
        if value.get('generation', 0) != epoch:
            return {}
        if value.get('phase') in {'writing', 'queued'} and time.time() - value.get('started', 0) > 600:
            return {}
        return value

    def generation(self):
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE name='cm_meta'").fetchone():
            return 0
        row = self.db.execute('SELECT generation FROM cm_meta WHERE contact=?', (self.owner,)).fetchone()
        return row[0] if row else 0

    def month(self, month, query=''):
        calendar = ExperienceCalendar(self.db, self.cfg, self.root).month(month)
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError('搜索内容最多200字。')
        events = calendar['events']
        groups = defaultdict(list)
        for event in events:
            groups[event['date']].append(event)
        projects = CreativeProjects(self.db, self.cfg, self.root)
        assets = defaultdict(list)
        for project in projects.list():
            for asset in projects.view(project)['assets']:
                day = datetime.fromtimestamp(asset['created'], TZ).strftime('%Y-%m-%d')
                assets[day].append(dict(asset, project_id=project['id']))
            if project.get('completed'):
                day = datetime.fromtimestamp(project['completed'], TZ).strftime('%Y-%m-%d')
                if day.startswith(month + '-'):
                    groups[day].append(dict(id='finished:' + project['id'], kind='milestone', title=project['title'],
                                            text='这件共同的事完成了。', created=project['completed'], date=day, project_id=project['id']))
        bookmarks = self._get(self.prefix + 'bookmarks', [])
        pages = []
        for day, entries in sorted(groups.items(), reverse=True):
            diary = next((e for e in entries if e.get('authored')), None)
            evidence = [e for e in entries if not e.get('authored')]
            title = diary['title'] if diary else '留在这一天的小事'
            body = diary['text'] if diary else ''
            content = title + '\n' + body + '\n'.join(e['title'] + e['text'] for e in evidence)
            if query.strip().casefold() not in content.casefold():
                continue
            illustration = self.selection(day)
            pages.append(dict(date=day, title=title, text=body, authored=bool(diary),
                              theme=illustration.get('theme', self.theme(content)), illustration=illustration, bookmarked=day in bookmarks,
                              events=evidence, assets=assets[day],
                              memorial=bool(assets[day]) or any(e['kind'] in {'scene', 'milestone'} for e in evidence)))
        return dict(month=month, cover=self.cover_selection(month), months=sorted(set(self.catalog()) | {month}, reverse=True), pages=pages, activity=self.activity(),
                    bookmark_dates=bookmarks, scope=self.cfg['bot_qq'] + ':' + self.owner,
                    generation=self.generation())

    def bookmark(self, day, enabled):
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) or type(enabled) is not bool:
            raise ValueError('请选择有效日期与书签状态。')
        if not any(p['date'] == day for p in self.month(day[:7])['pages']):
            raise ValueError('这一天没有可以夹书签的记录。')
        values = self._get(self.prefix + 'bookmarks', [])
        values = sorted((set(values) | {day}) if enabled else (set(values) - {day}), reverse=True)
        self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (self.prefix + 'bookmarks', json.dumps(values)))
        return dict(date=day, bookmarked=enabled)

    def illustration(self, theme):
        if theme not in THEMES:
            raise ValueError('没有这幅日记插画。')
        path = self.root / 'assets/diary/illustrations' / (theme + '.png')
        if not path.is_file():
            return ''
        return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')

    def art_choices(self):
        return [dict(kind='drawing', id=row['id'], scope=row['scope'], title=row.get('title', '未命名作品'))
                for row in ArtGallery(self.db, self.cfg['bot_qq'], self.root).entries()
                if row['scope'] in {'public', self.owner}]

    def selection(self, day):
        value = self._get(self.prefix + 'illustration:' + day, {})
        return self._available_selection(value)

    def _available_selection(self, value):
        if value.get('kind') == 'drawing':
            return next((a for a in self.art_choices() if a['id'] == value.get('id') and a['scope'] == value.get('scope')), {})
        return value if value.get('kind') == 'preset' and value.get('theme') in THEMES[1:] else {}

    def _checked_selection(self, selection):
        if selection == {'kind': 'auto'}:
            return None
        if isinstance(selection, dict) and set(selection) == {'kind', 'theme'} and selection['kind'] == 'preset' and selection['theme'] in THEMES[1:]:
            return selection
        if isinstance(selection, dict) and set(selection) == {'kind', 'id', 'scope'} and selection['kind'] == 'drawing':
            value = next((a for a in self.art_choices() if a['id'] == selection['id'] and a['scope'] == selection['scope']), None)
            if value:
                return value
            raise ValueError('这幅画不在当前可用的画册中。')
        raise ValueError('请选择主题插画或自己的画作。')

    def choose_art(self, day, selection):
        if (not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)
                or not isinstance(selection, dict) or not any(p['date'] == day for p in self.month(day[:7])['pages'])):
            raise ValueError('请选择已有记录的一天。')
        value = self._checked_selection(selection)
        if value is None:
            self.db.execute('DELETE FROM state WHERE key=?', (self.prefix + 'illustration:' + day,))
            return dict(saved=True)
        self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (self.prefix + 'illustration:' + day, json.dumps(value, ensure_ascii=False)))
        return dict(saved=True)

    def cover_selection(self, month):
        return self._available_selection(self._get(self.prefix + 'cover:' + month, {}))

    def choose_cover(self, month, selection):
        ExperienceCalendar(self.db, self.cfg, self.root).month(month)
        value = self._checked_selection(selection)
        key = self.prefix + 'cover:' + month
        if value is None:
            self.db.execute('DELETE FROM state WHERE key=?', (key,))
        else:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
        return dict(saved=True)

    def cover_art(self, month):
        ExperienceCalendar(self.db, self.cfg, self.root).month(month)
        selection = self.cover_selection(month)
        return self._drawing_art(selection)

    def page_art(self, day):
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError('日记日期无效。')
        if not any(p['date'] == day for p in self.month(day[:7])['pages']):
            return ''
        return self._drawing_art(self.selection(day))

    def _drawing_art(self, selection):
        if selection.get('kind') != 'drawing':
            return ''
        gallery = ArtGallery(self.db, self.cfg['bot_qq'], self.root)
        row = gallery.get(selection['id'], selection['scope'])
        return gallery.preview(row) if row else ''
