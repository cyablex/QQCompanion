# ruff: noqa: E402
"""Task/chat integration boundaries, using temporary data and fake transports."""
import asyncio
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_turn_actions import turns as turns, task
from test_singing import songs as songs, ready, incoming
from engine import DeliveryUnavailable, TaskStateChanged
from settings import atomic_json
from vpet_bridge import VPetBridge
from vpet_messages import PetMessages
from reply_queue import ReplyQueue
from stickers import StickerLibrary


def picture(e, contact='12345'):
    file = e.engine.sketchbook.folder(e.cfg, contact) / 'flow-picture.png'
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b'isolated generated image')
    row = dict(id='flow-picture', file=str(file), title='小鲸鱼', caption='这张画好了。', created=time.time())
    e.store.set(e.engine.sketchbook.key(e.cfg, contact), [row])
    return row


def queue_plugin(e, monkeypatch, event):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv('QQCOMPANION_ROOT', str(root))
    monkeypatch.syspath_prepend(str(root / 'astrbot'))
    spec = importlib.util.spec_from_file_location('companion_task_chat_queue_test', root / 'plugins/astrbot_plugin_qq_companion/main.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    plugin = module.QQCompanion.__new__(module.QQCompanion)
    plugin.store, plugin.engine, plugin.reply_queue = e.store, e.engine, ReplyQueue(e.store)
    plugin.pending, plugin.workers = {'12345': event}, {}
    plugin.schedule_memory = lambda contact: None
    notices = []

    async def notice(contact, text):
        notices.append(text)

    async def no_delay(seconds):
        return None

    plugin.error_notice = notice
    monkeypatch.setattr(module, 'asyncio', SimpleNamespace(sleep=no_delay, CancelledError=asyncio.CancelledError,
        create_task=asyncio.create_task))
    return plugin, notices


@pytest.mark.asyncio
async def test_legacy_mixed_answer_survives_without_replacing_the_song_receipt(turns):
    e = turns
    ready(e)
    incoming(e, '唱司凤版雨爱，也说说你的动态风格。')
    task(e, 'song.request', title='雨爱', artist='司凤')
    e.plan['messages'] = ['动态主要写我自己感兴趣的东西。']
    assert await e.engine.reply('12345')
    assert [m[1] for m in e.sent if not m[2]] == e.plan['messages']
    assert len([m for m in e.sent if m[2].get('song_file')]) == 1
    before = list(e.sent)
    await e.engine.reply('12345')
    assert e.sent == before


@pytest.mark.asyncio
async def test_mixed_chat_parses_sticker_and_mood_without_leaking_control_markers(turns):
    e = turns
    ready(e)
    incoming(e, '唱司凤版雨爱，也说说你喜欢什么颜色。')
    task(e, 'song.request', title='雨爱', artist='司凤')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。[心情:开心][表情:happy]'])
    e.cfg['send_memes'] = True
    stickers = StickerLibrary.__new__(StickerLibrary)
    stickers.items = {'happy': dict(tag='开心', meaning='高兴时使用')}
    e.engine.stickers = stickers
    assert await e.engine.reply('12345')
    assert e.sent[0][1:] == ('我喜欢蓝色。', {'sticker_id': 'happy'})
    assert e.sent[1][2].get('song_file')
    assert not any('[心情:' in m[1] or '[表情:' in m[1] for m in e.sent)
    before = list(e.sent)
    await e.engine.reply('12345')
    assert e.sent == before


@pytest.mark.asyncio
async def test_independent_chat_is_sent_before_waiting_for_drawing(turns, monkeypatch):
    e = turns
    incoming(e, '帮我画条鲸鱼，也说说你喜欢什么颜色。')
    task(e, 'drawing.create', brief='蓝色小鲸鱼')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。'])
    started, release = asyncio.Event(), asyncio.Event()

    async def create(*args, **kwargs):
        started.set()
        await release.wait()
        return picture(e)

    monkeypatch.setattr(e.engine.sketchbook, 'create', create)
    running = asyncio.create_task(e.engine.reply('12345'))
    await asyncio.wait_for(started.wait(), 3)
    while_waiting = list(e.sent)
    registered = e.store.db.execute('SELECT state FROM turn_tasks').fetchone()
    release.set()
    await running
    assert registered and registered['state'] in {'queued', 'running'}
    assert [(m[1], m[2]) for m in while_waiting] == [('我喜欢蓝色。', {})]
    assert len([m for m in e.sent if m[2].get('image_file')]) == 1


@pytest.mark.asyncio
async def test_image_preflight_failure_keeps_text_and_resumes_without_recreating(turns, monkeypatch):
    e = turns
    incoming(e, '帮我画条鲸鱼，也说说你喜欢什么颜色。')
    task(e, 'drawing.create', brief='蓝色小鲸鱼')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。'])
    created = []
    original = e.engine.send
    reject_image = True

    async def create(*args, **kwargs):
        created.append(True)
        return picture(e)

    async def send(contact, text, **options):
        if options.get('image_file') and reject_image:
            raise DeliveryUnavailable('image preflight failed, text transport available')
        return await original(contact, text, **options)

    monkeypatch.setattr(e.engine.sketchbook, 'create', create)
    e.engine.send = send
    try:
        await e.engine.reply('12345')
    except DeliveryUnavailable:
        pass
    assert [(m[1], m[2]) for m in e.sent] == [('我喜欢蓝色。', {})]
    assert e.store.db.execute('SELECT state FROM turn_tasks').fetchone()['state'] == 'prepared'
    reject_image = False
    await e.engine.reply('12345')
    assert len(created) == 1
    assert len([m for m in e.sent if not m[2]]) == 1
    assert len([m for m in e.sent if m[2].get('image_file')]) == 1


@pytest.mark.asyncio
async def test_unsent_chat_does_not_prevent_the_authorized_song_and_can_resume(turns):
    e = turns
    ready(e)
    incoming(e, '唱司凤版雨爱，也说说你喜欢什么颜色。')
    task(e, 'song.request', title='雨爱', artist='司凤')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。'])
    original = e.engine.send
    reject_text = True

    async def send(contact, text, **options):
        if not options and reject_text:
            raise DeliveryUnavailable('text definitely not sent')
        return await original(contact, text, **options)

    e.engine.send = send
    with pytest.raises(DeliveryUnavailable):
        await e.engine.reply('12345')
    assert len(e.sent) == 1 and e.sent[0][2].get('song_file')
    reject_text = False
    await e.engine.reply('12345')
    assert len([m for m in e.sent if m[2].get('song_file')]) == 1
    assert [m[1] for m in e.sent if not m[2]] == ['我喜欢蓝色。']
    assert len(e.calls) == 1


@pytest.mark.asyncio
async def test_real_reply_queue_keeps_unsent_image_and_does_not_repeat_chat(turns, monkeypatch):
    e = turns
    plugin, notices = queue_plugin(e, monkeypatch, 'image-request')
    incoming(e, '画条鲸鱼，也说说你喜欢什么颜色。', event='image-request')
    task(e, 'drawing.create', brief='小鲸鱼')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。'])
    original = e.engine.send
    reject_image = True
    created = []

    async def create(*args, **kwargs):
        created.append(True)
        return picture(e)

    async def send(contact, text, **options):
        if options.get('image_file') and reject_image:
            raise DeliveryUnavailable('image definitely not sent')
        return await original(contact, text, **options)

    monkeypatch.setattr(e.engine.sketchbook, 'create', create)
    e.engine.send = send
    plugin.reply_queue.enqueue('12345', 'image-request')
    await plugin.drain('12345')
    job = e.store.get('reply_job:12345')
    assert job and job['event'] == 'image-request' and job['attempts'] == 1
    assert job['due'] > time.time() and notices
    assert [m[1] for m in e.sent] == ['我喜欢蓝色。']
    assert e.store.db.execute('SELECT state FROM turn_tasks').fetchone()['state'] == 'prepared'
    reject_image = False
    job['due'] = time.time() - 1
    e.store.set('reply_job:12345', job)
    assert plugin.reply_queue.ready() == ['12345']
    await plugin.drain('12345')
    assert not e.store.get('reply_job:12345')
    assert len(created) == 1
    assert len([m for m in e.sent if m[2].get('image_file')]) == 1
    assert [m[1] for m in e.sent if not m[2]] == ['我喜欢蓝色。']


@pytest.mark.asyncio
async def test_background_song_completion_invalidates_the_old_waiting_reply(turns):
    e = turns
    incoming(e, '给我唱司凤版雨爱', event='old-song')
    await e.manager.handle('12345', e.engine, request=dict(title='雨爱', artist='司凤', source_url=''))
    request = e.store.db.execute('SELECT id,state FROM song_requests ORDER BY created DESC LIMIT 1').fetchone()
    assert request['state'] == 'waiting'
    e.sent.clear()
    incoming(e, '先陪我聊聊吧', event='new-chat')
    started, release = asyncio.Event(), asyncio.Event()
    generated_states = []

    async def generate(system, contexts, **options):
        if '【本轮动作核对】' in system:
            return json.dumps(dict(action='reply', messages=[], tasks=[])), None
        state = e.store.db.execute('SELECT state FROM song_requests WHERE id=?', (request['id'],)).fetchone()[0]
        generated_states.append(state)
        if len(generated_states) == 1:
            started.set()
            await release.wait()
            return '那首还没好，先陪你聊别的。', None
        return '可以呀，咱们聊聊你今天想画什么。', None

    e.engine.generate = generate
    running = asyncio.create_task(e.engine.reply('12345'))
    await asyncio.wait_for(started.wait(), 3)
    ready(e)
    await e.manager.deliver(request['id'])
    release.set()
    await running
    assert e.sent[0][2].get('song_file')
    assert not any('那首还没好' in m[1] for m in e.sent)
    assert e.sent[-1][1] == '可以呀，咱们聊聊你今天想画什么。'
    assert generated_states[0] == 'waiting' and 'sent' in generated_states[1:]


@pytest.mark.asyncio
async def test_repeated_task_changes_stop_after_one_refresh_without_sending_old_text(turns):
    e = turns
    incoming(e, '给我唱司凤版雨爱', event='old-song')
    await e.manager.handle('12345', e.engine, request=dict(title='雨爱', artist='司凤', source_url=''))
    request = e.store.db.execute('SELECT id FROM song_requests ORDER BY created DESC LIMIT 1').fetchone()
    e.sent.clear()
    incoming(e, '先陪我聊聊吧', event='new-chat')
    generations = []

    async def generate(system, contexts, **options):
        if '【本轮动作核对】' in system:
            return json.dumps(dict(action='reply', messages=[], tasks=[])), None
        generations.append(True)
        e.store.db.execute('UPDATE song_requests SET result=? WHERE id=?',
            ('Changed task fact ' + str(len(generations)), request['id']))
        return '那首还没好，先陪你聊别的。', None

    e.engine.generate = generate
    with pytest.raises(TaskStateChanged):
        await e.engine.reply('12345')
    assert len(generations) == 2 and not e.sent


@pytest.mark.asyncio
async def test_task_completion_during_voice_prepare_refreshes_before_sending(turns):
    e = turns
    incoming(e, '给我唱司凤版雨爱', event='old-song')
    await e.manager.handle('12345', e.engine, request=dict(title='雨爱', artist='司凤', source_url=''))
    request = e.store.db.execute('SELECT id FROM song_requests ORDER BY created DESC LIMIT 1').fetchone()
    e.sent.clear()
    incoming(e, '先陪我聊聊吧', event='new-chat')
    started, release = asyncio.Event(), asyncio.Event()
    generations, preparations = [], []

    async def generate(system, contexts, **options):
        if '【本轮动作核对】' in system:
            return json.dumps(dict(action='reply', messages=[], tasks=[])), None
        generations.append(True)
        return ('那首还没好，先陪你聊别的。' if len(generations) == 1 else '可以呀，咱们聊聊你今天想画什么。'), None

    async def prepare(contact, result):
        preparations.append(True)
        if len(preparations) == 1:
            started.set()
            await release.wait()
        return result

    e.engine.generate = generate
    e.engine.voice = SimpleNamespace(prepare=prepare)
    running = asyncio.create_task(e.engine.reply('12345'))
    await asyncio.wait_for(started.wait(), 3)
    ready(e)
    await e.manager.deliver(request['id'])
    release.set()
    await running
    assert len(generations) == len(preparations) == 2
    assert e.sent[0][2].get('song_file')
    assert [m[1] for m in e.sent if not m[2]] == ['可以呀，咱们聊聊你今天想画什么。']


@pytest.mark.asyncio
async def test_task_change_while_waiting_for_send_lock_is_rechecked_under_lock(turns):
    e = turns
    incoming(e, '给我唱司凤版雨爱', event='old-song')
    await e.manager.handle('12345', e.engine, request=dict(title='雨爱', artist='司凤', source_url=''))
    e.sent.clear()
    incoming(e, '先陪我聊聊吧', event='new-chat')
    decision_finished = asyncio.Event()
    generations = []

    async def generate(system, contexts, **options):
        if '【本轮动作核对】' in system:
            decision_finished.set()
            return json.dumps(dict(action='reply', messages=[], tasks=[])), None
        generations.append(True)
        return ('那首还没做好，先陪你聊别的。' if len(generations) == 1 else '可以呀，接着聊吧。'), None

    e.engine.generate = generate
    await e.engine.send_lock.acquire()
    running = asyncio.create_task(e.engine.reply('12345'))
    try:
        await asyncio.wait_for(decision_finished.wait(), 3)
        ready(e)
    finally:
        e.engine.send_lock.release()
    await running
    assert len(generations) == 2
    assert [m[1] for m in e.sent] == ['可以呀，接着聊吧。']


@pytest.mark.parametrize('stage', ['voice', 'send_lock', 'refresh_upstream_retry', 'refresh_send_retry', 'media_retry'])
@pytest.mark.asyncio
async def test_mixed_chat_refreshes_late_task_change_without_repeating_registered_drawing(turns, monkeypatch, stage):
    e = turns
    incoming(e, '给我唱司凤版雨爱', event='old-song')
    await e.manager.handle('12345', e.engine, request=dict(title='雨爱', artist='司凤', source_url=''))
    request = e.store.db.execute('SELECT id FROM song_requests ORDER BY created DESC LIMIT 1').fetchone()
    e.sent.clear()
    incoming(e, '画条鲸鱼，也说说你喜欢什么颜色。', event='mixed-drawing')
    started, release = asyncio.Event(), asyncio.Event()
    generations, preparations, created = [], [], []

    async def generate(system, contexts, **options):
        if '【本轮动作核对】' in system:
            return json.dumps(dict(action='reply', messages=[], tasks=[])), None
        generations.append(True)
        if stage == 'refresh_upstream_retry':
            if len(generations) == 2:
                raise RuntimeError('temporary upstream failure during refreshed reply')
            if len(generations) > 2:
                return '我也喜欢浅紫色。', None
        if stage in {'refresh_send_retry', 'media_retry'} and len(generations) >= 2:
            return '我也喜欢浅紫色。', None
        return json.dumps(dict(action='reply', messages=[], tasks=[dict(type='drawing.create', brief='小鲸鱼')],
            chat_messages=['我喜欢蓝色。' if len(generations) == 1 else '我也喜欢浅紫色。']), ensure_ascii=False), None

    async def prepare(contact, result):
        preparations.append(True)
        if len(preparations) == 1:
            started.set()
            if stage == 'voice':
                await release.wait()
        return result

    async def create(*args, **kwargs):
        created.append(True)
        return picture(e)

    e.engine.generate = generate
    e.engine.voice = SimpleNamespace(prepare=prepare)
    monkeypatch.setattr(e.engine.sketchbook, 'create', create)
    original_send = e.engine.send
    rejected = []

    async def send(contact, text, **options):
        if stage == 'media_retry' and options.get('image_file') and not rejected:
            rejected.append(True)
            raise DeliveryUnavailable('prepared image definitely not sent')
        if stage == 'refresh_send_retry' and text == '我也喜欢浅紫色。' and not options and not rejected:
            rejected.append(True)
            raise DeliveryUnavailable('refreshed ordinary answer definitely not sent')
        return await original_send(contact, text, **options)

    e.engine.send = send
    if stage == 'media_retry':
        plugin, notices = queue_plugin(e, monkeypatch, 'mixed-drawing')
        plugin.reply_queue.enqueue('12345', 'mixed-drawing')
    if stage != 'voice':
        await e.engine.send_lock.acquire()
    running = asyncio.create_task(plugin.drain('12345') if stage == 'media_retry' else e.engine.reply('12345'))
    try:
        await asyncio.wait_for(started.wait(), 3)
        if stage != 'voice':
            assert e.store.db.execute('SELECT count(*) FROM turn_tasks').fetchone()[0] == 1
        ready(e)
        if stage == 'voice':
            await e.manager.deliver(request['id'])
    finally:
        release.set()
        if stage != 'voice':
            e.engine.send_lock.release()
    if stage == 'media_retry':
        await running
        job = e.store.get('reply_job:12345')
        assert job and job['attempts'] == 1 and notices and rejected
        assert e.store.db.execute('SELECT state FROM turn_tasks').fetchone()[0] == 'prepared'
        assert len(created) == 1 and not e.sent
        job['due'] = time.time() - 1
        e.store.set('reply_job:12345', job)
        assert plugin.reply_queue.ready() == ['12345']
        await plugin.drain('12345')
        assert not e.store.get('reply_job:12345')
        assert len(generations) == 2
    elif stage == 'refresh_upstream_retry':
        with pytest.raises(RuntimeError, match='temporary upstream failure'):
            await running
        assert len(created) == 1 and not any(m[1] == '我喜欢蓝色。' for m in e.sent)
        assert await e.engine.reply('12345')
        assert len(generations) == 3
    elif stage == 'refresh_send_retry':
        with pytest.raises(DeliveryUnavailable, match='refreshed ordinary answer'):
            await running
        assert len(created) == 1 and rejected
        assert not any(m[1] in {'我喜欢蓝色。', '我也喜欢浅紫色。'} for m in e.sent)
        assert await e.engine.reply('12345')
        assert len(generations) >= 2
    else:
        assert await running
        assert len(generations) == 2
    assert len(preparations) >= 2
    assert not any(m[1] == '我喜欢蓝色。' for m in e.sent)
    assert [m[1] for m in e.sent if not m[2]] == ['我也喜欢浅紫色。']
    assert len(created) == e.store.db.execute('SELECT count(*) FROM turn_tasks').fetchone()[0] == 1
    assert len([m for m in e.sent if m[2].get('image_file')]) == 1
    before = list(e.sent)
    await e.engine.reply('12345')
    assert e.sent == before and len(created) == 1


@pytest.mark.asyncio
async def test_task_and_web_answer_keep_the_verified_source_on_sent_chat(turns, monkeypatch):
    e = turns
    ready(e)
    e.cfg['web_search'] = True
    incoming(e, '列一下曲库，也查一下测试城市的温度。')
    e.responses = ['[搜索:测试城市温度]', dict(action='reply', messages=[], tasks=[dict(type='song.list')],
        chat_messages=['测试城市现在20度。'])]
    url = 'https://example.org/verified-flow-source'

    async def lookup(*args, **kwargs):
        return dict(query='测试城市温度', sources=[dict(title='隔离测试来源', url=url, snippet='20度')])

    monkeypatch.setattr(e.engine.web, 'lookup', lookup)
    assert await e.engine.reply('12345')
    answer = [m[1] for m in e.sent if '20度' in m[1]]
    assert len(answer) == 1 and url in answer[0]
    assert sum(m[1].count(url) for m in e.sent) == 1


@pytest.mark.asyncio
async def test_new_input_during_drawing_does_not_repeat_old_chat_or_send_stale_image(turns, monkeypatch):
    e = turns
    incoming(e, '画条鲸鱼，也说说你喜欢什么颜色。', event='drawing')
    task(e, 'drawing.create', brief='小鲸鱼')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。'])
    started, release = asyncio.Event(), asyncio.Event()

    async def create(*args, **kwargs):
        started.set()
        await release.wait()
        return picture(e)

    monkeypatch.setattr(e.engine.sketchbook, 'create', create)
    running = asyncio.create_task(e.engine.reply('12345'))
    await asyncio.wait_for(started.wait(), 3)
    incoming(e, '换个话题，聊聊音乐吧', event='later')
    release.set()
    await running
    e.plan.update(messages=['好呀，聊聊音乐。'], tasks=[], chat_messages=[])
    assert await e.engine.reply('12345')
    assert [m[1] for m in e.sent] == ['我喜欢蓝色。', '好呀，聊聊音乐。']
    assert not any(m[2].get('image_file') for m in e.sent)


@pytest.mark.asyncio
async def test_forget_during_slow_task_cannot_restore_its_chat_or_media(turns, monkeypatch):
    e = turns
    incoming(e, '画条鲸鱼，也说说你喜欢什么颜色。')
    task(e, 'drawing.create', brief='小鲸鱼')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。'])
    generation = e.engine.memory_epoch('12345')
    started, release = asyncio.Event(), asyncio.Event()

    async def create(*args, **kwargs):
        started.set()
        await release.wait()
        return None if e.engine.memory_epoch('12345') != generation else picture(e)

    monkeypatch.setattr(e.engine.sketchbook, 'create', create)
    running = asyncio.create_task(e.engine.reply('12345'))
    await asyncio.wait_for(started.wait(), 3)
    before_forget = list(e.sent)
    e.store.forget('12345')
    release.set()
    await running
    assert before_forget and e.sent == before_forget
    assert not e.store.history('12345')
    assert not e.store.db.execute("SELECT 1 FROM state WHERE key LIKE 'turn_chat:%'").fetchone()
    assert not e.engine.sketchbook.records(e.cfg, '12345')


@pytest.mark.asyncio
async def test_desktop_mixed_reply_stays_in_desktop_outbox_and_deduplicates(turns):
    e = turns
    ready(e)
    atomic_json(e.root / 'config/vpet.json', dict(enabled=True))
    bridge = VPetBridge(SimpleNamespace(store=e.store, engine=e.engine, send_private=e.engine.send), e.root)
    e.engine.delivery_router = bridge.route_delivery
    event = 'vpet:' + uuid.uuid4().hex
    incoming(e, '唱司凤版雨爱，也说说你喜欢什么颜色。', event=event)
    bridge.record_source(event, e.cfg)
    task(e, 'song.request', title='雨爱', artist='司凤')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。'])
    assert await e.engine.reply('12345')
    messages = PetMessages(e.store, e.cfg).pending()
    assert not e.sent
    assert messages[0]['text'] == '我喜欢蓝色。' and messages[0]['media'] is None
    assert len([m for m in messages if m['media'] and m['media']['kind'] == 'song']) == 1
    await e.engine.reply('12345')
    assert PetMessages(e.store, e.cfg).pending() == messages and not e.sent


@pytest.mark.asyncio
async def test_group_mixed_reply_stays_in_group_and_answers_before_song(turns):
    e = turns
    ready(e)
    contact = 'group:99999'
    text = '唱司凤版雨爱，也说说你喜欢什么颜色。'
    e.store.incoming('group-mixed', contact, '[群成员 测试] ' + text)
    e.store.set('song_origin:' + contact, dict(event_id='group-mixed', text=text, requester='77777', created=time.time()))
    task(e, 'song.request', title='雨爱', artist='司凤')
    e.plan.update(messages=[], chat_messages=['我喜欢蓝色。'])
    assert await e.groups.engine.reply(contact)
    outgoing = [data for name, data in e.native if name == 'send_group_msg']
    assert len(outgoing) == 2 and all(m['group_id'] == 99999 for m in outgoing)
    assert outgoing[0]['message'][0]['type'] == 'text'
    assert outgoing[0]['message'][0]['data']['text'] == '我喜欢蓝色。'
    assert outgoing[1]['message'][0]['type'] == 'record'
    assert not e.sent
