import asyncio
import sys
import uuid
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from test_engine import setup as engine_setup  # noqa: E402,F401
from vpet_bridge import DELIVERY, VPetBridge  # noqa: E402
from settings import atomic_json  # noqa: E402
import vpet_runtime  # noqa: E402
import game_mode  # noqa: E402


@pytest.fixture
def pet(engine_setup, tmp_path):  # noqa: F811
    store, cfg, engine, sent, prompts = engine_setup
    atomic_json(tmp_path / 'config/vpet.json', dict(enabled=True))
    bridge = VPetBridge(SimpleNamespace(store=store, engine=engine), tmp_path)
    original = engine.send

    async def route(contact, text, **media):
        if DELIVERY.get() is not None:
            return await bridge.deliver(contact, text, **media)
        return await original(contact, text)

    engine.send = route
    return store, cfg, engine, sent, prompts, bridge, tmp_path


@pytest.mark.asyncio
async def test_touch_uses_existing_engine_and_shared_history_but_no_qq_send(pet):
    store, cfg, engine, sent, prompts, bridge, _ = pet
    request = dict(id=uuid.uuid4().hex, kind='head')
    result = await bridge.interact(request)
    assert result['messages'] == ['hello'] and result['saved']
    assert not sent and len(prompts) == 1
    history = store.history(cfg['owner_qq'])
    assert '桌宠互动' in history[0]['content'] and history[-1]['content'] == 'hello'
    assert await bridge.interact(request) == result
    assert len(prompts) == 1
    assert (await bridge.interact(dict(id=uuid.uuid4().hex, kind='body')))['state'] == 'cooldown'
    assert len(store.history(cfg['owner_qq'])) == 2
    store.incoming('qq-after-pet', cfg['owner_qq'], '刚才摸了哪里？')
    await engine.reply(cfg['owner_qq'])
    assert sent and '桌宠互动' in str(prompts[-1])


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [dict(id='x', kind='head'), dict(id='a' * 32, kind='command'),
                                      dict(id='a' * 32, kind='head', contact='99999')])
async def test_desktop_does_not_accept_commands_or_client_supplied_owner(pet, payload):
    with pytest.raises(ValueError):
        await pet[5].interact(payload)
    assert not pet[0].history(pet[1]['owner_qq']) and not pet[4]


@pytest.mark.asyncio
async def test_game_mode_blocks_interaction_and_status_never_calls_model(pet):
    store, cfg, _, _, prompts, bridge, _ = pet
    cfg['game_mode'] = True
    result = await bridge.interact(dict(id='a' * 32, kind='head'))
    assert result['state'] == 'paused' and not result['saved']
    assert (await bridge.state())['game_mode']
    assert not prompts and not store.history(cfg['owner_qq'])


@pytest.mark.asyncio
async def test_forget_cannot_resurrect_interaction_receipt_after_model_returns(pet):
    store, cfg, engine, sent, _, bridge, _ = pet
    started, release = asyncio.Event(), asyncio.Event()
    original = engine.generate

    async def generate(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    engine.generate = generate
    task = asyncio.create_task(bridge.interact(dict(id='b' * 32, kind='head')))
    await started.wait()
    store.forget(cfg['owner_qq'])
    release.set()
    result = await task
    assert result['state'] == 'forgotten'
    assert not sent and not store.history(cfg['owner_qq'])
    assert not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'vpet:%'").fetchone()


def test_game_mode_closes_only_owned_pet_and_restores_requested_state(tmp_path, monkeypatch):
    monkeypatch.setattr(game_mode, 'ROOT', tmp_path)
    stopped, starts = [], []
    own = SimpleNamespace(pid=1, info={'exe': str(tmp_path / 'vpet/host/DafeiyuPet.exe')},
                          terminate=lambda: stopped.append(1), wait=lambda timeout: None)
    other = SimpleNamespace(pid=2, info={'exe': str(tmp_path / 'other/DafeiyuPet.exe')})
    monkeypatch.setattr(vpet_runtime.psutil, 'process_iter', lambda _: [own, other])
    monkeypatch.setattr(game_mode, 'close_local_voice', lambda: dict(stopped=[3], denied=[]))
    cfg = dict(revision=1, game_mode=False)
    atomic_json(tmp_path / 'config/vpet.json', dict(enabled=True))
    outcome = game_mode.set_game_mode(True, cfg)
    assert outcome['stopped'] == [3, 1] and stopped == [1]
    assert vpet_runtime.preferences(tmp_path)['enabled']
    monkeypatch.setattr(vpet_runtime, 'reconcile', lambda root, cfg: starts.append((root, cfg['game_mode'])) or dict(stopped=[], denied=[]))
    game_mode.set_game_mode(False, dict(cfg, game_mode=True))
    assert starts == [(tmp_path, False)]


def test_pet_start_obeys_preparation_and_game_mode_without_other_processes(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='准备'):
        vpet_runtime.set_enabled(True, tmp_path, dict(game_mode=False))
    target = vpet_runtime.executable(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'placeholder')
    atomic_json(tmp_path / 'config/vpet.json', dict(enabled=False))
    (tmp_path / 'config/vpet-bridge.token').write_text('local-test-token')
    monkeypatch.setattr(vpet_runtime, 'processes', lambda root: [])
    called = []
    monkeypatch.setattr(vpet_runtime.subprocess, 'Popen', lambda *args, **kwargs: called.append(args))
    vpet_runtime.set_enabled(True, tmp_path, dict(game_mode=True))
    assert not called
    vpet_runtime.reconcile(tmp_path, dict(game_mode=False))
    assert len(called) == 1 and called[0][0][0] == str(target)
    vpet_runtime.set_enabled(False, tmp_path, dict(game_mode=False))
    assert not vpet_runtime.preferences(tmp_path)['enabled']


def test_navigation_is_restricted_to_known_console_pages(tmp_path):
    with pytest.raises(ValueError):
        vpet_runtime.open_console('https://example.org', tmp_path)
    assert not (tmp_path / 'data/desktop-navigation.json').exists()


@pytest.mark.asyncio
async def test_loopback_bridge_requires_token_and_rejects_browser_origins(pet, monkeypatch):
    import vpet_bridge
    _, _, _, _, prompts, bridge, _ = pet
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    monkeypatch.setattr(vpet_bridge, 'PORT', port)
    monkeypatch.setattr(vpet_bridge, 'local_token', lambda _: 'local-test-only-token')
    bridge.start()

    def request(headers):
        call = urllib.request.Request(f'http://127.0.0.1:{port}/status', headers=headers)
        try:
            with urllib.request.urlopen(call, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, None

    try:
        assert (await asyncio.to_thread(request, {}))[0] == 403
        auth = {'Authorization': 'Bearer local-test-only-token'}
        assert (await asyncio.to_thread(request, dict(auth, Origin='https://example.org')))[0] == 403
        status, response = await asyncio.to_thread(request, auth)
        assert status == 200 and response['data']['ready']
        assert not prompts
    finally:
        await bridge.close()
