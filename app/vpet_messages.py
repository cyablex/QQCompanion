"""Durable, owner-scoped desktop delivery queue; model actions are never replayed."""

import json
import re
import time
import uuid
from pathlib import Path


class PetMessages:
    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg
        self.owner = cfg['owner_qq']
        self.prefix = f"vpet_msg:{cfg['bot_qq']}:{self.owner}:"

    def generation(self):
        row = self.store.db.execute('SELECT generation FROM cm_meta WHERE contact=?', (self.owner,)).fetchone()
        return row[0] if row else 0

    def put(self, key, index, text, options):
        ident = uuid.uuid5(uuid.NAMESPACE_URL, key + ':' + str(index)).hex
        existing = self.store.get(self.prefix + ident)
        if existing:
            return ident
        media = None
        for field, kind in (('song_file', 'song'), ('voice_file', 'voice'), ('image_file', 'image'), ('sticker_file', 'sticker')):
            if options.get(field):
                media = dict(kind=kind, path=str(Path(options[field]).resolve()), song_id=options.get('song_id', ''),
                             sticker_id=options.get('sticker_id', ''))
                break
        self.store.set(self.prefix + ident, dict(id=ident, text=text, media=media, created=time.time(),
                                               generation=self.generation(), acknowledged=False))
        return ident

    def get(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident):
            raise ValueError('消息编号无效。')
        value = self.store.get(self.prefix + ident)
        if not value or value['generation'] != self.generation():
            raise ValueError('这条桌面消息已不可用。')
        return value

    @staticmethod
    def public(value):
        media = value['media']
        return dict(id=value['id'], text=value['text'], created=value['created'],
                    media=dict(kind=media['kind'], url='/media/' + value['id']) if media else None)

    def pending(self):
        records = [json.loads(row[0]) for row in self.store.db.execute(
            'SELECT value FROM state WHERE key LIKE ?', (self.prefix + '%',))]
        records = [item for item in records if not item['acknowledged'] and item['generation'] == self.generation()]
        records.sort(key=lambda item: item['created'])
        return [self.public(item) for item in records[:20]]

    def acknowledge(self, ids):
        if not isinstance(ids, list) or len(ids) > 20:
            raise ValueError('消息确认无效。')
        records = [self.get(ident) for ident in ids]
        for record in records:
            record['acknowledged'] = True
            self.store.set(self.prefix + record['id'], record)
        return dict(acknowledged=len(records))
