"""Resolve a reply's original transport from trusted, persisted event metadata."""


def route(store, cfg, contact, *, event_id=None, read_through=None):
    if not event_id and read_through is not None:
        row = store.db.execute('SELECT id FROM events WHERE contact=? AND created=? ORDER BY rowid DESC LIMIT 1',
                               (contact, read_through)).fetchone()
        event_id = row['id'] if row else None
    if not event_id or not event_id.startswith('vpet:'):
        return 'qq'
    source = store.get('event_source:' + event_id, {})
    if (contact != cfg['owner_qq'] or source.get('channel') != 'vpet'
            or source.get('owner') != cfg['owner_qq'] or source.get('bot') != cfg['bot_qq']):
        return 'unavailable'
    return 'vpet'
