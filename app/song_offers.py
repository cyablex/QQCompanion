"""A confirmed, optional song invitation; acceptance uses the ordinary task ledger."""

import json
import re
import time


def key(cfg, contact):
    return f"song_offer:{cfg['bot_qq']}:{contact}"


def current(engine, contact, cfg, now=None):
    now = time.time() if now is None else now
    offer = engine.store.get(key(cfg, contact), {})
    if (offer.get('state') != 'offered' or offer.get('expires', 0) <= now
            or offer.get('owner') != cfg['owner_qq'] or offer.get('bot') != cfg['bot_qq']
            or offer.get('generation') != engine.memory_epoch(contact)):
        return None
    return offer


def context(singing, cfg):
    if not cfg.get('singing_enabled', True) or not singing:
        return ''
    songs = [dict(id=s['id'], title=s['title'], artist=s['artist']) for s in singing.library.list()
             if s['state'] == 'ready' and singing.library.allowed_file(s['id'], singing.library.audio_path(s['id']))][:20]
    return ('\n【可选唱歌邀请】可以偶尔自然地说想给对方唱首歌，但先问是否想听，不直接发长语音。'
            '只在对方表达过音乐兴趣、近期聊到相关歌，或主人相处语境自然时选择；不对陌生好友硬推。'
            '确实想邀请时在本轮JSON加song_offer:"真实曲目id"，歌名与征求同意的话由程序附上，正文不用重复。'
            '没有合适的就省略该字段，不编造新曲、制作进度或已发送。当前可邀请的真实成品：'
            + json.dumps(songs, ensure_ascii=False))


def prepare(engine, contact, cfg, result, now):
    ident = result.get('song_offer')
    if not ident:
        return result, None
    if not cfg.get('singing_enabled', True) or not engine.singing:
        return None, None
    previous = engine.store.get(key(cfg, contact), {})
    gap = 86400 if contact == cfg['owner_qq'] else 2 * 86400
    if previous.get('at', 0) + gap > now:
        return None, None
    song = engine.singing.library.get(ident)
    if not song or not engine.singing.library.allowed_file(ident, song['file']):
        return None, None
    text = '\n'.join(result['messages']).strip()
    invitation = f"我给你唱一首《{song['title']}》吧，想听的话就说一声。"
    text = (text[:max(0, 1100 - len(invitation))] + '\n' + invitation).strip()
    return dict(result, messages=[text]), dict(song_id=ident, title=song['title'], artist=song['artist'],
        owner=cfg['owner_qq'], bot=cfg['bot_qq'], generation=engine.memory_epoch(contact),
        state='offered', at=now, expires=now + 12 * 3600, text=text)


def accept(engine, contact, cfg, ident, latest, through):
    offer = current(engine, contact, cfg)
    row = engine.store.db.execute("SELECT text FROM messages WHERE contact=? AND role='assistant' AND created<=? ORDER BY id DESC LIMIT 1",
                                  (contact, through)).fetchone()
    # An isolated 'yes' belongs to the most recent invitation, not an unrelated
    # later conversation. The model decides intent; these gates bind the action
    # to the actual delivered offer and reject explicit refusals/quotations.
    if (not offer or offer['song_id'] != ident or not row or row[0] != offer['text']
            or not latest.strip() or re.search(r'不(?:要|想|用|听|唱|行|可以)|别(?:唱|发)|拒绝|假如|如果|[“”「」"]', latest)):
        raise ValueError('没有可由当前回答接续的已确认唱歌邀请。')
    song = engine.singing.library.get(ident)
    if not song or not engine.singing.library.allowed_file(ident, song['file']):
        raise ValueError('邀请中的歌曲已不可用。')
    engine.store.set(key(cfg, contact), dict(offer, state='accepted'))
    return dict(title=song['title'], artist=song['artist'], source_url='')
