# ruff: noqa: E402
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from test_engine import setup as setup
from test_singing import songs as songs, ready, incoming
from test_turn_actions import turns as turns, task
from engine import DeliveryUnavailable
from outreach import outcome
from song_offers import key as offer_key
from store import TZ


@pytest.fixture(autouse=True)
def daytime_clock(monkeypatch):
    # Assessing at noon must also deliver at noon. Otherwise these tests fail
    # during real quiet hours, before reaching the drawing/invitation path.
    moment = datetime.now(TZ).replace(hour=12, minute=0, second=0, microsecond=0)

    class Daytime(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr('engine.datetime', Daytime)
    monkeypatch.setattr('store.time.time', lambda: moment.timestamp())


@pytest.mark.asyncio
async def test_invalid_proactive_output_retries_soon_without_sending_or_long_skip(setup, monkeypatch):
    store, cfg, engine, sent, prompts = setup
    now = datetime.now(TZ).replace(hour=12, minute=0)
    store.incoming('old', cfg['owner_qq'], '聊过的音乐', now.timestamp() - 4 * 3600)
    calls = []

    async def generate(*args, **kwargs):
        calls.append(True)
        return 'invalid structured response', None

    engine.generate = generate
    assert not await engine.proactive(now)
    result = store.get(f"outreach_result:{cfg['bot_qq']}:{cfg['owner_qq']}")
    assert not sent and len(calls) == 2
    assert result['state'] == 'failed' and result['error'] == 'JSONDecodeError'
    assert result['next'] == now.timestamp() + 300
    assert store.get(f"outreach_due:{cfg['bot_qq']}:owner") == result['next']
    assert not await engine.proactive(now)
    assert len(calls) == 2


def test_repeated_failures_stop_fast_retries_and_success_resets_failure_count(setup):
    store, cfg, engine, *_ = setup
    contact = cfg['owner_qq']
    for index, delay in enumerate([300, 900, 7200, 7200]):
        outcome(engine, cfg, contact, 'proactive', 1000 + index, 'failed', 'network')
        result = store.get(f"outreach_result:{cfg['bot_qq']}:{contact}")
        assert result['failures'] == index + 1 and result['next'] == 1000 + index + delay
    outcome(engine, cfg, contact, 'proactive', 2000, 'sent')
    assert store.get(f"outreach_result:{cfg['bot_qq']}:{contact}")['failures'] == 0


@pytest.mark.asyncio
async def test_drawing_failure_sends_only_real_ordinary_text(setup, monkeypatch):
    store, cfg, engine, sent, _ = setup
    cfg['autonomous_drawing'] = True
    now = datetime.now(TZ).replace(hour=12, minute=0)
    store.incoming('old', cfg['owner_qq'], '聊过的小画', now.timestamp() - 4 * 3600)

    async def generate(*args, **kwargs):
        return json.dumps(dict(action='reply', messages=['突然想起你之前说的那段旋律。', '给你看我画的小鲸鱼。'],
                               drawing_brief='晒太阳的小鲸鱼')), None

    async def create(*args, **kwargs):
        raise ValueError('drawing failed')

    engine.generate = generate
    monkeypatch.setattr(engine.sketchbook, 'create', create)
    assert await engine.proactive(now)
    assert sent == [(cfg['owner_qq'], '突然想起你之前说的那段旋律。')]


@pytest.mark.asyncio
async def test_song_invitation_is_real_but_audio_waits_for_acceptance(turns, monkeypatch):
    e = turns
    ready(e)
    e.cfg['proactive'] = True
    now = datetime.now(TZ).replace(hour=12, minute=0).timestamp()
    monkeypatch.setattr('store.time.time', lambda: now)
    incoming(e, '我挺喜欢听歌的', event='earlier')
    e.store.db.execute('UPDATE events SET created=?', (now - 4 * 3600,))
    e.store.db.execute('UPDATE messages SET created=?', (now - 4 * 3600,))
    song = e.manager.library.list()[0]
    e.plan.update(messages=['刚想起我们聊过的歌。'], tasks=[], song_offer=song['id'])
    assert await e.engine.proactive(datetime.fromtimestamp(now, TZ))
    invitation = e.store.get(offer_key(e.cfg, '12345'))
    assert invitation['state'] == 'offered'
    assert len(e.sent) == 1 and not e.sent[0][2] and song['title'] in e.sent[0][1]
    assert 'song_offer' in e.calls[0][0] and '只输出一个JSON对象' in e.calls[0][0]
    monkeypatch.setattr('store.time.time', lambda: now + 1)
    incoming(e, '好呀，唱吧', event='accepted')
    e.plan.update(messages=[], song_offer=None)
    task(e, 'song.accept', song_id=song['id'])
    assert await e.engine.reply('12345')
    assert len([m for m in e.sent if m[2].get('song_file')]) == 1
    await e.engine.reply('12345')
    assert len([m for m in e.sent if m[2].get('song_file')]) == 1
    assert e.store.get(offer_key(e.cfg, '12345'))['state'] == 'accepted'
    e.store.forget('12345')
    assert not e.store.get(offer_key(e.cfg, '12345'))


@pytest.mark.asyncio
@pytest.mark.parametrize('cause', ['refusal', 'expired', 'other_conversation', 'wrong_song', 'unknown_delivery'])
async def test_song_acceptance_cannot_use_an_unrelated_or_unconfirmed_offer(turns, cause):
    e = turns
    ready(e)
    song = e.manager.library.list()[0]
    now = datetime.now(TZ).timestamp()
    text = '我给你唱一首《雨爱》吧，想听的话就说一声。'
    e.store.message('12345', 'assistant', text, now - 30)
    offer = dict(song_id=song['id'], title=song['title'], artist=song['artist'], state='offered', text=text, at=now - 30,
                 expires=now + 3600, generation=0, owner=e.cfg['owner_qq'], bot=e.cfg['bot_qq'])
    if cause == 'expired':
        offer['expires'] = now - 1
    if cause == 'other_conversation':
        e.store.message('12345', 'assistant', '聊聊午饭。', now - 10)
    if cause != 'unknown_delivery':
        e.store.set(offer_key(e.cfg, '12345'), offer)
    incoming(e, '不要唱' if cause == 'refusal' else '好呀，唱吧')
    task(e, 'song.accept', song_id='f' * 32 if cause == 'wrong_song' else song['id'])
    await e.engine.reply('12345')
    assert not any(m[2].get('song_file') for m in e.sent)


@pytest.mark.asyncio
async def test_known_unsent_invitation_creates_no_acceptance_record(turns):
    e = turns
    ready(e)
    e.cfg['proactive'] = True
    now = datetime.now(TZ).replace(hour=12, minute=0)
    e.store.incoming('old', '12345', '我喜欢听歌', now.timestamp() - 4 * 3600)
    e.plan.update(messages=['想听听我们之前聊过的歌。'], tasks=[], song_offer=e.manager.library.list()[0]['id'])

    async def fail(*args, **kwargs):
        raise DeliveryUnavailable('not sent')

    e.engine.send = fail
    with pytest.raises(DeliveryUnavailable):
        await e.engine.proactive(now)
    assert not e.store.get(offer_key(e.cfg, '12345'))
