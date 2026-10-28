# ruff: noqa: E402
import json
import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from test_vpet import pet as pet
from test_engine import setup as engine_setup  # noqa: F401
from experience_calendar import ExperienceCalendar
from settings import atomic_json
from store import TZ

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prepare_pet_poses import prepare


def payload(cfg, kind='rice_placed', generation=0):
    return dict(id=uuid.uuid4().hex, kind=kind, scope=cfg['bot_qq'] + ':' + cfg['owner_qq'], generation=generation)


@pytest.mark.asyncio
async def test_food_scene_never_becomes_history_or_calendar_evidence(pet):
    store, cfg, engine, sent, prompts, bridge, root = pet
    store.incoming('question', cfg['owner_qq'], '上一件事情怎么样了？')
    through = store.last_incoming(cfg['owner_qq'])
    event = payload(cfg)
    assert await bridge.scene(event) == dict(saved=False, state='local')
    assert await bridge.scene(event) == dict(saved=False, state='local')
    assert store.last_incoming(cfg['owner_qq']) == through
    assert not prompts and not sent
    assert not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'vpet_scene:%'").fetchone()
    calendar = ExperienceCalendar(store.db, cfg, root).month(datetime.now(TZ).strftime('%Y-%m'))
    assert not [e for e in calendar['events'] if e['kind'] == 'scene']
    assert await engine.reply(cfg['owner_qq'])
    assert '放了一碗白米饭' not in str(prompts)


@pytest.mark.asyncio
async def test_scene_during_model_generation_keeps_the_pending_answer(pet):
    store, cfg, engine, sent, _, bridge, _ = pet
    entered, release = asyncio.Event(), asyncio.Event()
    original = engine.generate

    async def delayed(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    engine.generate = delayed
    store.incoming('active-question', cfg['owner_qq'], '你刚才说的呢？')
    answer = asyncio.create_task(engine.reply(cfg['owner_qq']))
    await asyncio.wait_for(entered.wait(), 2)
    assert not (await bridge.scene(payload(cfg, 'rice_finished')))['saved']
    release.set()
    assert await asyncio.wait_for(answer, 2)
    assert sent == [(cfg['owner_qq'], 'hello')]




@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['wrong_scope', 'paused', 'game', 'disabled'])
async def test_scene_rejects_unavailable_or_different_companion(pet, mode):
    store, cfg, _, _, prompts, bridge, root = pet
    event = payload(cfg)
    if mode == 'wrong_scope':
        event['scope'] = 'another:owner'
    elif mode == 'paused':
        cfg['paused'] = True
    elif mode == 'game':
        cfg['game_mode'] = True
    else:
        atomic_json(root / 'config/vpet.json', dict(enabled=False))
    assert not (await bridge.scene(event))['saved']
    assert not store.history(cfg['owner_qq']) and not prompts


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [dict(kind='do_command'), dict(kind=[]), dict(generation=True), dict(text='write arbitrary text')])
async def test_scene_accepts_only_fixed_observed_events(pet, change):
    store, cfg, _, _, _, bridge, _ = pet
    event = dict(payload(cfg), **change)
    with pytest.raises(ValueError):
        await bridge.scene(event)
    assert not store.history(cfg['owner_qq'])


def test_optional_high_resolution_sheet_conversion_keeps_alpha_and_full_pose(tmp_path):
    folder = tmp_path / 'assets/pet/hq'
    folder.mkdir(parents=True)
    sheet = Image.new('RGBA', (1536, 1024))
    draw = ImageDraw.Draw(sheet)
    for row in range(2):
        for col in range(3):
            draw.rounded_rectangle((col * 512 + 80, row * 525 + 35, col * 512 + 430, min(995, row * 525 + 490)), fill='blue', radius=30)
    sheet.save(folder / 'daily-sheet.png')
    target = prepare(tmp_path)
    with Image.open(target / 'idle.png') as idle:
        assert idle.size == (1024, 1024) and idle.getpixel((0, 0))[3] == 0
        assert idle.getbbox()[3] <= 974
    assert len(json.loads((target / 'poses.json').read_text())['poses']) == 6
    assert not (tmp_path / 'config/vpet.json').exists()
