"""Real, delivered song invitations; acceptance never relies on imagined assets."""

import json
import re
import time

from song_library import normalized


def key(cfg, contact):
    return f"song_offer:{cfg['bot_qq']}:{contact}"


def current(engine, contact, cfg, now=None, *, requester=None, through=None):
    now = time.time() if now is None else now
    offer = engine.store.get(key(cfg, contact), {})
    if (offer.get('state') != 'offered' or offer.get('expires', 0) <= now
            or offer.get('owner') != cfg['owner_qq'] or offer.get('bot') != cfg['bot_qq']
            or offer.get('generation') != engine.memory_epoch(contact)
            or (contact.startswith('group:') and offer.get('requester') != requester)):
        return None
    # Bind to the actual delivered bubble, including voice/image decorations.
    # A current turn's independent answer must not invalidate its own acceptance.
    boundary = engine.store.db.execute(
        "SELECT id FROM messages WHERE contact=? AND role='user' AND created<=? ORDER BY id DESC LIMIT 1",
        (contact, through)).fetchone() if through is not None else None
    row = engine.store.db.execute(
        "SELECT id,text FROM messages WHERE contact=? AND role='assistant' AND id<? AND created<=? ORDER BY id DESC LIMIT 1",
        (contact, boundary['id'] if boundary else 2**63 - 1, now if through is None else through)).fetchone()
    if not row or (row['id'] != offer['message_id'] if offer.get('message_id') else row['text'] != offer.get('text')):
        return None
    song = engine.singing.library.get(offer.get('song_id', '')) if engine.singing else None
    if not song or not engine.singing.library.allowed_file(song['id'], song['file']):
        return None
    return offer


def validate_invitation(messages, singing, *, registered=False):
    """Validate assistant invitations, not the user's choice/consent."""
    text = '\n'.join(messages)
    outside = re.sub(r'“[^”]*”|‘[^’]*’|"[^"\n]*"', '', text)
    direct = bool(re.search(r'(?:给你|为你).{0,8}(?:唱|来一首)|(?:我|本鲸).{0,8}唱.{0,8}(?:给你|一首)|听我唱', outside))
    suggestion = bool(re.search(r'(?:要不要|想不想).{0,16}(?:听|唱)|(?:我们|一起|先|给你|发你|来).{0,8}(?:听|试听)|听一耳朵|想听.{0,8}(?:就|的话)', outside))
    music = bool(re.search(r'首歌|歌曲|歌声|曲库|唱歌|听歌', text))
    if not music and singing:
        music = any(normalized(s['title']) in normalized(text) for s in singing.library.list() if s['state'] == 'ready')
    if not direct and not (suggestion and music):
        return
    if re.search(r'前\s*\d+\s*秒|新旧(?:版|版本)|(?:A.?B|新旧).{0,3}对比', text, re.I):
        raise ValueError('当前聊天邀请只支持真实完整成品，不能承诺片段试听或新旧对比。')
    if not registered:
        raise ValueError('唱歌邀请需要登记真实曲目：主动联系用song_offer，聊天用song.offer。')


def context(singing, cfg):
    if not cfg.get('singing_enabled', True) or not singing:
        return ''
    songs = [dict(id=s['id'], title=s['title'], artist=s['artist']) for s in singing.library.list()
             if s['state'] == 'ready' and singing.library.allowed_file(s['id'], singing.library.audio_path(s['id']))][:20]
    return ('\n【可选唱歌邀请】可以偶尔自然地说想给对方唱首歌，但先问是否想听，不直接发长语音。'
            '只在对方表达过音乐兴趣、近期聊到相关歌，或主人相处语境自然时选择；不对陌生好友硬推。'
            '确实想邀请时在本轮JSON加song_offer:"真实曲目id"，歌名与征求同意的话由程序附上，正文不用重复。'
            '当前聊天只提供完整成品，不承诺前30秒、局部片段、新旧版本对比等未接入的试听流程。'
            '没有合适的就省略该字段，不编造新曲、制作进度或已发送。当前可邀请的真实成品：'
            + json.dumps(songs, ensure_ascii=False))


def prepare(engine, contact, cfg, result, now, *, autonomous=True, requester=None):
    ident = result.get('song_offer')
    if not ident:
        return result, None
    if not cfg.get('singing_enabled', True) or not engine.singing:
        return None, None
    previous = engine.store.get(key(cfg, contact), {})
    gap = 86400 if contact == cfg['owner_qq'] else 2 * 86400
    if autonomous and previous.get('at', 0) + gap > now:
        return None, None
    song = engine.singing.library.get(ident)
    if not song or not engine.singing.library.allowed_file(ident, song['file']):
        return None, None
    text = '\n'.join(result['messages']).strip()
    validate_invitation(result['messages'], engine.singing, registered=True)
    invitation = f"我给你唱一首《{song['title']}》（{song['artist']}版）吧，想听的话就说一声。"
    text = (text[:max(0, 1100 - len(invitation))] + '\n' + invitation).strip()
    record = dict(song_id=ident, title=song['title'], artist=song['artist'],
        owner=cfg['owner_qq'], bot=cfg['bot_qq'], generation=engine.memory_epoch(contact),
        requester=requester or contact, state='offered', at=now, expires=now + 12 * 3600, text=text)
    return dict(result, messages=[text], song_invitation=record), record


def remember(engine, contact, record, history_line):
    row = engine.store.db.execute(
        "SELECT id FROM messages WHERE contact=? AND role='assistant' ORDER BY id DESC LIMIT 1", (contact,)).fetchone()
    engine.store.set(key(dict(bot_qq=record['bot']), contact), dict(record, message_id=row['id'], text=history_line))


def acceptance_text(latest):
    # Intent still comes from the turn planner. These gates exclude known
    # refusals/quotations from inheriting a previously offered song.
    return bool(latest.strip()) and not re.search(
        r'不(?:要|想|用|听|唱|行|可以)|别(?:唱|发)|拒绝|假如|如果|[“”「」"]', latest)


def accept(engine, contact, cfg, ident, latest, through, *, requester=None):
    offer = current(engine, contact, cfg, requester=requester, through=through)
    if not offer or offer['song_id'] != ident or not acceptance_text(latest):
        raise ValueError('没有可由当前回答接续的已确认唱歌邀请。')
    song = engine.singing.library.get(ident)
    engine.store.set(key(cfg, contact), dict(offer, state='accepted'))
    return dict(title=song['title'], artist=song['artist'], source_url='')
