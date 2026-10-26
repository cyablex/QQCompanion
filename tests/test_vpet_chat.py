# ruff: noqa: E402
"""Desktop chat uses the real orchestration and file permissions without QQ sends."""
import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_vpet import pet as pet
from test_engine import setup as engine_setup  # noqa: F401
from test_turn_actions import turns as turns, task
from test_singing import songs as songs, ready
from engine import DeliveryUnavailable
from settings import atomic_json
from message_channels import route
from vpet_messages import PetMessages
from vpet_bridge import VPetBridge


@pytest.mark.asyncio
async def test_chat_retries_share_history_without_replaying_or_qq(pet):
    store, cfg, engine, sent, prompts, bridge, _ = pet
    value = dict(id=uuid.uuid4().hex, text='刚才桌面上发生了什么？')
    assert (await bridge.chat(value))['accepted']
    await asyncio.gather(*bridge.chat_tasks)
    await bridge.chat(value)
    assert len(prompts) == 1 and not sent
    assert '通过桌面形象' in prompts[0][0]
    assert store.history(cfg['owner_qq'])[-1]['content'] == 'hello'
    outbox = PetMessages(store, cfg)
    messages = outbox.pending()
    assert len(messages) == 1 and messages[0]['text'] == 'hello'
    assert PetMessages(store, cfg).pending() == messages
    outbox.acknowledge([messages[0]['id']])
    assert not outbox.pending()
    store.incoming('qq-following', cfg['owner_qq'], '你还记得刚才的话吗')
    assert await engine.reply(cfg['owner_qq'])
    assert sent and len(prompts) == 2 and not outbox.pending()


@pytest.mark.asyncio
async def test_new_qq_message_cancels_inflight_desktop_reply(pet):
    store, cfg, engine, sent, _, bridge, _ = pet
    started, release = asyncio.Event(), asyncio.Event()
    original = engine.generate

    async def generate(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    engine.generate = generate
    await bridge.chat(dict(id=uuid.uuid4().hex, text='先回答桌面的这条'))
    await started.wait()
    store.incoming('later-qq', cfg['owner_qq'], '换个话题')
    release.set()
    await asyncio.gather(*bridge.chat_tasks)
    assert not sent and not PetMessages(store, cfg).pending()
    assert await engine.reply(cfg['owner_qq']) and sent


@pytest.mark.asyncio
async def test_desktop_origin_cannot_fallback_to_qq_after_metadata_loss(pet):
    store, cfg, engine, sent, _, bridge, _ = pet
    event = 'vpet:' + uuid.uuid4().hex
    store.incoming(event, cfg['owner_qq'], '请留在桌面')
    assert route(store, cfg, cfg['owner_qq'], event_id=event) == 'unavailable'
    with pytest.raises(DeliveryUnavailable):
        await engine.deliver(cfg['owner_qq'], 'song_notice', dict(action='reply', messages=['完成'], revision=1,
            read_through=store.last_incoming(cfg['owner_qq']), delivery_event=event), key='lost-source')
    assert not sent
    bridge.record_source(event, cfg)
    box = PetMessages(store, cfg)
    ident = box.put('saved', 0, '旧消息', {})
    store.forget(cfg['owner_qq'])
    assert not box.pending()
    with pytest.raises(ValueError):
        box.get(ident)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['game', 'disabled', 'owner'])
async def test_chat_respects_paused_entry_and_never_accepts_another_owner(pet, change):
    store, cfg, _, _, prompts, bridge, root = pet
    if change == 'game':
        cfg['game_mode'] = True
    elif change == 'disabled':
        atomic_json(root / 'config/vpet.json', dict(enabled=False))
    value = dict(id=uuid.uuid4().hex, text='你好')
    if change == 'owner':
        value['owner'] = '99999'
    with pytest.raises(ValueError):
        await bridge.chat(value)
    assert not prompts and not store.history(cfg['owner_qq'])


def desktop(e):
    atomic_json(e.root / 'config/vpet.json', dict(enabled=True))
    bridge = VPetBridge(SimpleNamespace(store=e.store, engine=e.engine, send_private=e.engine.send), e.root)
    bridge.plugin.enqueue_reply = lambda contact, event: asyncio.create_task(e.engine.reply(contact))
    e.engine.delivery_router = bridge.route_delivery
    return bridge


@pytest.mark.asyncio
async def test_desktop_song_action_reuses_task_engine_and_real_file(turns):
    e = turns
    bridge = desktop(e)
    song = ready(e)
    task(e, 'song.choose', song_id=song['id'])
    await bridge.chat(dict(id=uuid.uuid4().hex, text='你挑一首唱给我听'))
    await asyncio.gather(*bridge.chat_tasks)
    assert not e.sent and len(e.calls) == 1 and not e.model_calls
    messages = PetMessages(e.store, e.cfg).pending()
    assert messages[-1]['media']['kind'] == 'song'
    assert 'path' not in str(messages)
    response = await bridge.media(messages[-1]['id'])
    assert response.path == Path(song['file']).resolve()
    assert e.store.db.execute('SELECT state FROM song_requests').fetchone()[0] == 'sent'


@pytest.mark.asyncio
async def test_late_song_stays_at_original_desktop_after_new_qq_message(turns):
    e = turns
    bridge = desktop(e)
    event = 'vpet:' + uuid.uuid4().hex
    e.store.incoming(event, '12345', '唱这首歌')
    bridge.record_source(event, e.cfg)
    song = ready(e)
    e.store.incoming('new-qq', '12345', 'QQ的另一个话题')
    assert await e.engine.deliver('12345', 'song_reply', dict(action='reply', messages=['成品'],
        revision=1, read_through=e.store.last_incoming('12345'), memory_generation=0,
        delivery_event=event, song_id=song['id'], song_file=song['file']), key='late-song')
    assert not e.sent and PetMessages(e.store, e.cfg).pending()[0]['media']['kind'] == 'song'


@pytest.mark.asyncio
async def test_sticker_only_reply_has_authenticated_real_media(pet, tmp_path):
    store, cfg, engine, sent, _, bridge, _ = pet
    image = tmp_path / 'sticker.png'
    image.write_bytes(b'test-fixture-image')
    engine.stickers = SimpleNamespace(items={'happy': dict(file=str(image), tag='开心')})
    event = 'vpet:' + uuid.uuid4().hex
    store.incoming(event, cfg['owner_qq'], '晚安')
    bridge.record_source(event, cfg)
    assert await engine.deliver(cfg['owner_qq'], 'reply', dict(action='reply', messages=[''], sticker_id='happy',
        revision=1, read_through=store.last_incoming(cfg['owner_qq'])), key='sticker-only')
    message = PetMessages(store, cfg).pending()[0]
    assert not sent and message['media']['kind'] == 'sticker'
    assert (await bridge.media(message['id'])).path == image
    engine.stickers.items.clear()
    with pytest.raises(ValueError):
        await bridge.media(message['id'])


@pytest.mark.asyncio
async def test_closing_bridge_cancels_chat_before_store_closes(pet):
    _, _, engine, sent, _, bridge, _ = pet
    started = asyncio.Event()

    async def generate(*args, **kwargs):
        started.set()
        await asyncio.Future()

    engine.generate = generate
    await bridge.chat(dict(id=uuid.uuid4().hex, text='中断演练'))
    await started.wait()
    await bridge.close()
    assert not bridge.chat_tasks and not sent


@pytest.mark.asyncio
async def test_command_retry_and_forget_remove_private_receipt_content(pet):
    store, cfg, _, sent, prompts, bridge, _ = pet
    store.remember(cfg['owner_qq'], 'private fixture fact')
    request = dict(id=uuid.uuid4().hex, text='/记忆')
    assert 'private fixture fact' in (await bridge.chat(request))['confirmation']
    forget = dict(id=uuid.uuid4().hex, text='/忘记全部')
    confirmation = (await bridge.chat(forget))['confirmation']
    assert '已清除' in confirmation
    assert not store.history(cfg['owner_qq']) and not store.memories(cfg['owner_qq'])
    assert 'private fixture fact' not in str((await bridge.chat(request)))
    store.remember(cfg['owner_qq'], 'new memory after forget')
    assert (await bridge.chat(forget))['confirmation'] == confirmation
    assert store.memories(cfg['owner_qq']) and not sent and not prompts
