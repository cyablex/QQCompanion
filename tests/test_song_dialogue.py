"""Song invitations and follow-ups keep evidence through real task/delivery paths."""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from test_singing import songs as songs, ready, incoming  # noqa: E402,F401
from test_turn_actions import turns as turns, task  # noqa: E402,F401
from song_offers import key, current, prepare  # noqa: E402
from turn_actions import TurnActions  # noqa: E402
from reply_parser import parse_chat_reply, parse_decision  # noqa: E402
from store import TZ  # noqa: E402

DSML = '<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="execute_song_request">\n<｜｜DSML｜｜ parameter name="title" string="true">time machine</｜｜DSML｜｜ parameter>\n</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,answer', [('song.accept', '听一下'), ('song.request', '听一下'), ('song.request', 'mj apanay')])
async def test_delivered_conversation_offer_supplies_the_confirmed_song(turns, kind, answer):
    e = turns
    song = ready(e, 'time machine', 'mj apanay', ())
    incoming(e, '刚好有空，聊聊听歌吧', event='before-offer')
    task(e, 'song.offer', song_id=song['id'])
    assert await e.engine.reply('12345')
    saved = e.store.get(key(e.cfg, '12345'))
    assert saved['state'] == 'offered' and saved['message_id']
    assert saved['artist'] == 'mj apanay'
    assert not any(m[2].get('song_file') for m in e.sent)
    incoming(e, answer, event='accept')
    if kind == 'song.accept':
        task(e, kind, song_id=song['id'])
    else:
        task(e, kind, title='time machine', artist='mj apanay')
    assert await e.engine.reply('12345')
    assert sum(m[2].get('song_id') == song['id'] for m in e.sent) == 1
    assert not any('想听哪首' in m[1] or '哪位歌手' in m[1] for m in e.sent)
    await e.engine.reply('12345')
    assert sum(bool(m[2].get('song_file')) for m in e.sent) == 1


@pytest.mark.asyncio
async def test_artist_first_then_title_is_not_forgotten(turns):
    e = turns
    song = ready(e, 'time machine', 'mj apanay', ())
    incoming(e, '唱mj apanay的', event='artist')
    task(e, 'song.request', title='', artist='mj apanay')
    await e.engine.reply('12345')
    assert e.store.get('song_pending:67890:12345:12345')['artist'] == 'mj apanay'
    incoming(e, 'time machine', event='title')
    task(e, 'song.request', title='time machine', artist='')
    await e.engine.reply('12345')
    assert e.sent[-1][2].get('song_id') == song['id']
    assert not any('哪位歌手' in m[1] for m in e.sent)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['same', 'new_title'])
async def test_confirmed_artist_is_preserved_only_for_same_selection(turns, change):
    e = turns
    song = ready(e, 'time machine', 'mj apanay', ())
    e.store.set('song_pending:67890:12345:12345',dict(title='time machine', artist='mj apanay',created=time.time()-10,expires=time.time()+3600))
    incoming(e, '继续唱这首' if change == 'same' else '唱See You Again')
    task(e, 'song.request', title='time machine' if change == 'same' else 'See You Again', artist='mj apanay')
    await e.engine.reply('12345')
    if change == 'same':
        assert e.sent[-1][2].get('song_id') == song['id']
    else:
        assert not e.sent[-1][2].get('song_file')
        assert 'See You Again' in e.sent[-1][1] and '哪位歌手' in e.sent[-1][1]


@pytest.mark.asyncio
async def test_offer_acceptance_survives_independent_answer_in_same_turn(turns, monkeypatch):
    e = turns
    song = ready(e)
    # A coarse Windows clock can give the answer and source message equal times.
    now = time.time()
    monkeypatch.setattr('store.time.time', lambda: now)
    incoming(e, '闲聊一下', event='offer-source')
    task(e, 'song.offer', song_id=song['id'])
    await e.engine.reply('12345')
    incoming(e, '好呀，唱吧。你喜欢蓝色吗？', event='accept')
    task(e, 'song.accept', song_id=song['id'])
    e.plan['chat_messages'] = ['喜欢，浅蓝色看着很舒服。']
    await e.engine.reply('12345')
    assert any(m[1] == e.plan['chat_messages'][0] for m in e.sent)
    assert e.sent[-1][2].get('song_file')


@pytest.mark.asyncio
@pytest.mark.parametrize('decoration', ['voice', 'image', 'split'])
async def test_invitation_is_bound_to_actual_media_or_last_bubble(songs, decoration):
    e = songs
    song = ready(e)
    incoming(e, '先聊一下', event='source')
    result, record = prepare(e.engine, '12345', e.cfg,
        dict(action='reply', messages=[], song_offer=song['id'], revision=1),time.time(),autonomous=False)
    if decoration == 'voice':
        monkey_voice = '[语音] ' + result['messages'][0]
        e.store.message('12345', 'assistant', monkey_voice)
    elif decoration == 'image':
        e.store.message('12345', 'assistant', result['messages'][0]+'\n[发送自己画的简笔画]')
    else:
        e.store.message('12345', 'assistant', '刚想起之前聊过的歌。')
        e.store.message('12345', 'assistant', result['messages'][0])
    from song_offers import remember
    remember(e.engine,'12345',record,e.store.db.execute("SELECT text FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 1").fetchone()[0])
    incoming(e, '好呀', event='accept')
    assert current(e.engine,'12345',e.cfg,through=e.store.last_incoming('12345'))


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['expired', 'other_chat', 'refused', 'quoted', 'cancelled', 'deleted'])
@pytest.mark.parametrize('kind', ['song.accept', 'song.request'])
async def test_bad_offer_does_not_supply_context_or_send_song(turns, case, kind):
    e = turns
    song = ready(e)
    incoming(e, '聊聊听歌', event='source')
    task(e,'song.offer',song_id=song['id'])
    await e.engine.reply('12345')
    if case == 'expired':
        record=e.store.get(key(e.cfg,'12345'))
        e.store.set(key(e.cfg,'12345'),dict(record,expires=time.time()-1))
    elif case == 'other_chat':
        e.store.message('12345','assistant','换个话题，说说午饭吧。')
    elif case == 'cancelled':
        incoming(e,'不听了',event='cancel')
        task(e,'song.cancel')
        await e.engine.reply('12345')
    elif case == 'deleted':
        e.manager.library.delete(song['id'])
    text = '不要唱' if case == 'refused' else '如果我说“听一下”会怎么样' if case == 'quoted' else '听一下'
    incoming(e,text,event='accept')
    if kind == 'song.accept':
        task(e,kind,song_id=song['id'])
    else:
        task(e,kind,title=song['title'],artist=song['artist'])
    await e.engine.reply('12345')
    assert not any(m[2].get('song_file') for m in e.sent)


@pytest.mark.asyncio
async def test_group_invitation_cannot_be_accepted_by_another_member(turns):
    e = turns
    song=ready(e)
    now=time.time()
    text='我给你唱一首雨爱吧。'
    e.store.message('group:99999','assistant',text,now-1)
    mid=e.store.db.execute('SELECT max(id) FROM messages').fetchone()[0]
    e.store.set(key(e.cfg,'group:99999'),dict(song_id=song['id'],title=song['title'],artist=song['artist'],state='offered',at=now-1,expires=now+3600,owner=e.cfg['owner_qq'],bot=e.cfg['bot_qq'],generation=0,requester='11111',message_id=mid))
    assert current(e.groups.engine,'group:99999',e.cfg,requester='11111')
    assert not current(e.groups.engine,'group:99999',e.cfg,requester='22222')
    assert not e.manager.selection_context('group:99999','22222')


@pytest.mark.asyncio
async def test_unregistered_preview_invitation_is_repaired_before_proactive_send(turns, monkeypatch):
    e=turns
    song=ready(e,'time machine','mj apanay',())
    e.cfg['proactive']=True
    monkeypatch.setattr('engine.quiet',lambda _:False)
    now=datetime.now(TZ).replace(hour=12,minute=0)
    e.store.incoming('old','12345','我喜欢听歌',now.timestamp()-4*3600)
    e.responses=[dict(action='reply',messages=['有空我们先听 Time Machine 前30秒的新旧版本？']),
                 dict(action='reply',messages=['刚想起之前聊过的旋律。'],song_offer=song['id'])]
    assert await e.engine.proactive(now)
    assert len(e.calls)==2
    assert all('30秒' not in m[1] and '新旧' not in m[1] for m in e.sent)
    assert e.store.get(key(e.cfg,'12345'))['song_id']==song['id']


@pytest.mark.asyncio
@pytest.mark.parametrize('markup',[DSML,'<tool_call>{"name":"execute_song_request"}</tool_call>','<function=execute_song_request>{}</function>'])
async def test_internal_call_is_rewritten_into_a_real_task_before_sending(turns,markup):
    e=turns
    song=ready(e,'time machine','mj apanay',())
    incoming(e,'给我唱time machine，mj apanay版')
    e.responses=[markup,dict(action='reply',messages=[],tasks=[dict(type='song.request',title='time machine',artist='mj apanay')])]
    await e.engine.reply('12345')
    assert len(e.calls)==2
    assert len(e.sent)==1 and e.sent[0][2].get('song_id')==song['id']
    assert 'DSML' not in str(e.sent) and 'tool_call' not in str(e.sent)


@pytest.mark.asyncio
async def test_repeated_protocol_errors_have_bounded_plain_recovery(turns):
    e=turns
    incoming(e,'给我唱一首歌')
    e.responses=[DSML]*6
    await e.engine.reply('12345')
    assert len(e.calls)<=6 and e.sent
    assert not any('DSML' in m[1] or m[2].get('song_file') for m in e.sent)
    assert not e.store.db.execute('SELECT 1 FROM song_requests').fetchone()


@pytest.mark.parametrize('decoder',['turn','chat','structured','escaped'])
def test_all_reply_decoders_reject_internal_call_markup(decoder):
    cfg=dict(owner_qq='12345',revision=1)
    with pytest.raises(ValueError):
        if decoder=='turn':
            TurnActions.decode(DSML,cfg,0,allow_unplanned=True)
        elif decoder=='chat':
            parse_chat_reply(DSML,cfg,0,contact='12345')
        elif decoder=='escaped':
            TurnActions.decode(json.dumps(dict(action='reply',messages=[DSML],tasks=[])).replace('<','\\u003c'),cfg,0)
        else:
            parse_decision(json.dumps(dict(action='reply',messages=[DSML])),cfg,0)


@pytest.mark.asyncio
async def test_final_delivery_also_rejects_internal_markup(songs):
    e=songs
    with pytest.raises(ValueError):
        await e.engine.deliver('12345','reply',dict(action='reply',messages=[DSML],revision=1))
    assert not e.sent


@pytest.mark.asyncio
async def test_conversational_invitation_repair_keeps_chat_without_duplicate_invites(turns):
    e=turns
    song=ready(e,'time machine','mj apanay',())
    incoming(e,'说说你喜欢的颜色吧')
    e.responses=['我喜欢浅蓝色。要不要先听 Time Machine？',
                 dict(action='reply',messages=[],tasks=[dict(type='song.offer',song_id=song['id'])])]
    await e.engine.reply('12345')
    assert sum('想听的话' in m[1] for m in e.sent)==1
    assert not any('要不要先听' in m[1] for m in e.sent)
    assert any('我喜欢浅蓝色' in m[1] for m in e.sent)
    assert e.store.get(key(e.cfg,'12345'))['state']=='offered'


@pytest.mark.asyncio
async def test_known_unsent_conversation_offer_retries_once_without_playing(turns):
    from engine import DeliveryUnavailable
    e=turns
    song=ready(e)
    incoming(e,'聊聊听歌')
    task(e,'song.offer',song_id=song['id'])
    original=e.engine.send
    failed=False
    async def send(*args,**kwargs):
        nonlocal failed
        if not failed:
            failed=True
            raise DeliveryUnavailable('not sent')
        return await original(*args,**kwargs)
    e.engine.send=send
    with pytest.raises(DeliveryUnavailable):
        await e.engine.reply('12345')
    assert not e.store.get(key(e.cfg,'12345'))
    assert e.store.db.execute('SELECT state FROM turn_tasks').fetchone()[0]=='queued'
    assert await e.engine.reply('12345')
    assert len(e.sent)==1 and not e.sent[0][2]
    assert e.store.get(key(e.cfg,'12345'))['state']=='offered'
    await e.engine.reply('12345')
    assert len(e.sent)==1


@pytest.mark.asyncio
async def test_unknown_conversation_invitation_is_not_registered_or_replayed(turns):
    e=turns
    song=ready(e)
    incoming(e,'聊聊听歌')
    task(e,'song.offer',song_id=song['id'])
    attempts=[]
    async def unknown(*args,**kwargs):
        attempts.append(True)
        raise RuntimeError('missing receipt')
    e.engine.send=unknown
    await e.engine.reply('12345')
    assert e.store.db.execute('SELECT state FROM turn_tasks').fetchone()[0]=='unknown'
    assert not e.store.get(key(e.cfg,'12345'))
    await e.engine.reply('12345')
    assert attempts==[True]


def test_historical_protocol_leak_is_not_reused_as_assistant_instruction(songs):
    e=songs
    e.store.message('12345','assistant',DSML)
    e.store.message('12345','user','刚才发的这是什么？')
    history=e.engine.conversation_history('12345')
    assert 'DSML' not in str(history)
    assert history[-1]['content']=='刚才发的这是什么？'
    assert e.store.db.execute("SELECT text FROM messages WHERE role='assistant'").fetchone()[0]==DSML


@pytest.mark.asyncio
async def test_model_guessed_artist_cannot_erase_the_confirmed_offer(turns):
    e=turns
    song=ready(e)
    incoming(e,'闲聊',event='source')
    task(e,'song.offer',song_id=song['id'])
    await e.engine.reply('12345')
    incoming(e,'听一下',event='accept')
    task(e,'song.request',title='',artist='杨丞琳')
    await e.engine.reply('12345')
    assert e.sent[-1][2].get('song_id')==song['id']
    assert not any('哪位歌手' in m[1] or '想听哪首' in m[1] for m in e.sent)


@pytest.mark.asyncio
async def test_new_topic_stays_chat_and_does_not_accept_old_invitation(turns):
    e=turns
    song=ready(e)
    incoming(e,'聊聊听歌',event='source')
    task(e,'song.offer',song_id=song['id'])
    await e.engine.reply('12345')
    incoming(e,'今天画的小鲸鱼是什么颜色？',event='other-topic')
    e.plan.update(tasks=[],messages=['浅蓝色呀，看着很清爽。'])
    await e.engine.reply('12345')
    assert e.sent[-1][1]=='浅蓝色呀，看着很清爽。'
    assert not any(m[2].get('song_file') for m in e.sent)
    assert not current(e.engine,'12345',e.cfg)


@pytest.mark.parametrize('text',['台词里“我给你唱”只是一句对白。', '想不想聊聊 Time Machine 的歌词？', 'Time Machine 前30秒的音色还有问题。'])
def test_music_discussion_is_not_mistaken_for_an_invitation(songs,text):
    ready(songs,'time machine','mj apanay',())
    from song_offers import validate_invitation
    validate_invitation([text],songs.manager)


@pytest.mark.asyncio
async def test_progress_of_new_selection_does_not_report_an_older_song_as_sent(turns):
    e=turns
    old=ready(e)
    incoming(e,'唱司凤版雨爱',event='old')
    task(e,'song.request',title='雨爱',artist='司凤')
    await e.engine.reply('12345')
    newer=ready(e,'time machine','mj apanay',())
    incoming(e,'聊聊听歌',event='offer')
    task(e,'song.offer',song_id=newer['id'])
    await e.engine.reply('12345')
    incoming(e,'怎么还没有唱',event='status')
    task(e,'song.status')
    await e.engine.reply('12345')
    assert 'time machine' in e.sent[-1][1] and '还没开始制作或发送' in e.sent[-1][1]
    assert old['title'] not in e.sent[-1][1]
    assert sum(bool(m[2].get('song_file')) for m in e.sent)==1
    incoming(e,'好，那就唱吧',event='confirmed')
    task(e,'song.accept',song_id=newer['id'])
    await e.engine.reply('12345')
    assert e.sent[-1][2].get('song_id')==newer['id']
