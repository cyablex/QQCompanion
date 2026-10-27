import asyncio
import json
from datetime import datetime

import pytest

from test_engine import setup as engine_setup  # noqa: F401
from test_vpet import pet as pet  # noqa: F401
from test_daily_diary import messages, prose
from creative_projects import CreativeProjects
from daily_diary import DailyDiary
from diary_book import DiaryBook
from store import TZ
from PIL import Image


@pytest.mark.asyncio
async def test_reader_preserves_authored_text_bookmark_sources_and_forget(engine_setup, tmp_path):  # noqa: F811
    store, cfg, engine, _, _ = engine_setup
    cfg['memory_enabled'] = True
    messages(store, cfg['owner_qq'])

    async def generate(kind, system, context, **kwargs):
        source = json.loads(context[0]['content'])['messages']
        return json.dumps(dict(title='把副歌记在这里', text=prose(), source_ids=[s['id'] for s in source]))

    engine.generate_aux = generate
    assert await DailyDiary(store).write(engine, cfg)
    book = DiaryBook(store.db, cfg, tmp_path)
    day = datetime.now(TZ).strftime('%Y-%m-%d')
    page = book.month(day[:7])['pages'][0]
    assert page['authored'] and page['text'] == prose() and page['theme'] == 'song'
    assert book.activity()['phase'] == 'ready'
    assert book.bookmark(day, True)['bookmarked']
    assert DiaryBook(store.db, cfg, tmp_path).month(day[:7])['pages'][0]['bookmarked']
    assert not DiaryBook(store.db, dict(cfg, owner_qq='99999'), tmp_path).month(day[:7])['pages']
    store.forget(cfg['owner_qq'])
    assert not book.month(day[:7])['pages'] and not book.activity()
    assert not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'diary_book:%'").fetchone()


def test_memorial_references_only_actual_scoped_project_events(engine_setup, tmp_path):  # noqa: F811
    store, cfg, _, _, _ = engine_setup
    projects = CreativeProjects(store.db, cfg, tmp_path)
    project = projects.create('一起留下的小作品', 'other')
    projects.note(project['id'], '下次再继续', 'companion')
    finished = projects.update(project['id'], 'finished', '')
    assert projects.update(project['id'], 'finished', '')['completed'] == finished['completed']
    book = DiaryBook(store.db, cfg, tmp_path)
    result = book.month(datetime.now(TZ).strftime('%Y-%m'))
    page = result['pages'][0]
    assert not page['authored'] and page['text'] == '' and page['memorial']
    assert any(e['id'] == 'finished:' + project['id'] for e in page['events'])
    assert not book.month(result['month'], '不在记录中的话')['pages']
    assert not DiaryBook(store.db, dict(cfg, bot_qq='99999'), tmp_path).month(result['month'])['pages']


@pytest.mark.parametrize('day,enabled', [('2026-99-42', True), ('../secret', True), ('2026-10-27', 'yes')])
def test_bookmark_needs_an_existing_day(engine_setup, tmp_path, day, enabled):  # noqa: F811
    store, cfg, *_ = engine_setup
    with pytest.raises(ValueError):
        DiaryBook(store.db, cfg, tmp_path).bookmark(day, enabled)


def test_illustrations_are_fixed_local_assets_and_optional(engine_setup, tmp_path):  # noqa: F811
    store, cfg, *_ = engine_setup
    book = DiaryBook(store.db, cfg, tmp_path)
    assert book.illustration('cover') == ''
    folder = tmp_path / 'assets/diary/illustrations'
    folder.mkdir(parents=True)
    (folder/'cover.png').write_bytes(b'local-generated-art')
    assert book.illustration('cover').startswith('data:image/png;base64,')
    with pytest.raises(ValueError):
        book.illustration('../../config/user.json')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [False, True])
async def test_actual_diary_generation_exposes_writing_then_ready_or_failed(pet, failure):  # noqa: F811
    store, cfg, engine, sent, _, bridge, root = pet
    cfg['memory_enabled'] = True
    messages(store, cfg['owner_qq'])
    bridge.plugin.daily_diary = DailyDiary(store)
    entered, release = asyncio.Event(), asyncio.Event()

    async def generate(kind, system, context, **kwargs):
        entered.set()
        await release.wait()
        if failure:
            raise RuntimeError('isolated provider failure')
        source = json.loads(context[0]['content'])['messages']
        return json.dumps(dict(title='今天的副歌', text=prose(), source_ids=[s['id'] for s in source]))

    engine.generate_aux = generate
    day = datetime.now(TZ).strftime('%Y-%m-%d')
    result = await bridge.write_diary(dict(day=day, scope=cfg['bot_qq']+':'+cfg['owner_qq'], generation=0))
    assert result['accepted']
    await asyncio.wait_for(entered.wait(), 2)
    assert (await bridge.state())['activity'] == '正在写日记'
    assert DiaryBook(store.db, cfg, root).activity()['phase'] == 'writing'
    release.set()
    await asyncio.gather(*list(bridge.chat_tasks))
    assert DiaryBook(store.db, cfg, root).activity()['phase'] == ('failed' if failure else 'ready')
    assert not sent


@pytest.mark.asyncio
async def test_manual_diary_rejects_stale_identity_and_generation(pet):  # noqa: F811
    store, cfg, _, _, _, bridge, _ = pet
    cfg['memory_enabled'] = True
    day = datetime.now(TZ).strftime('%Y-%m-%d')
    for scope, generation in [('other:owner', 0), (cfg['bot_qq']+':'+cfg['owner_qq'], 1)]:
        with pytest.raises(ValueError):
            await bridge.write_diary(dict(day=day, scope=scope, generation=generation))
    assert not bridge.chat_tasks and not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'diary_activity:%'").fetchone()


def test_daily_art_is_owned_persistent_and_falls_back_when_deleted(engine_setup, tmp_path):  # noqa: F811
    store, cfg, *_ = engine_setup
    CreativeProjects(store.db, cfg, tmp_path).create('留下这一天', 'other')
    for index, scope in enumerate([cfg['owner_qq'], 'public', '99999']):
        ident = 'abc'[index] * 32
        path = tmp_path / 'data/drawings' / cfg['bot_qq'] / scope / (ident + '.png')
        path.parent.mkdir(parents=True)
        Image.new('RGB', (10, 10), 'blue').save(path)
        store.set(f"sketches:{cfg['bot_qq']}:{scope}", [dict(id=ident, title='自己的画' if index == 0 else '样例', file=str(path))])
    book = DiaryBook(store.db, cfg, tmp_path)
    day = datetime.now(TZ).strftime('%Y-%m-%d')
    assert {a['scope'] for a in book.art_choices()} == {cfg['owner_qq'], 'public'}
    with pytest.raises(ValueError):
        book.choose_art(day, dict(kind='drawing', id='c'*32, scope='99999'))
    assert book.choose_art(day, dict(kind='drawing', id='a'*32, scope=cfg['owner_qq']))['saved']
    assert '自己的画' in str(book.month(day[:7])['pages'][0]['illustration'])
    assert book.page_art(day).startswith('data:image/png;base64,')
    (tmp_path/'data/drawings'/cfg['bot_qq']/cfg['owner_qq']/('a'*32+'.png')).unlink()
    assert not book.page_art(day) and not book.month(day[:7])['pages'][0]['illustration']
    book.choose_art(day, dict(kind='preset', theme='memory'))
    assert book.month(day[:7])['pages'][0]['theme'] == 'memory'
    book.choose_art(day, dict(kind='auto'))
    assert not book.selection(day)
    book.choose_cover(day[:7], dict(kind='preset', theme='night'))
    assert book.month(day[:7])['cover']['theme'] == 'night'
    assert not book.month('2026-09')['cover']
    book.choose_cover(day[:7], dict(kind='drawing', id='b'*32, scope='public'))
    assert book.cover_art(day[:7]).startswith('data:image/png;base64,')
    with pytest.raises(ValueError):
        book.choose_cover('2026-13', dict(kind='auto'))
    store.forget(cfg['owner_qq'])
    assert not book.page_art(day) and not book.cover_selection(day[:7])


@pytest.mark.asyncio
async def test_manual_diary_with_insufficient_material_reports_skipped_without_fake_completion(pet):  # noqa: F811
    store, cfg, _, sent, prompts, bridge, root = pet
    cfg['memory_enabled'] = True
    bridge.plugin.daily_diary = DailyDiary(store)
    await bridge.write_diary(dict(day=datetime.now(TZ).strftime('%Y-%m-%d'), scope=cfg['bot_qq']+':'+cfg['owner_qq'], generation=0))
    await asyncio.gather(*list(bridge.chat_tasks))
    assert DiaryBook(store.db, cfg, root).activity()['phase'] == 'skipped'
    assert not sent and not prompts


def test_restart_marks_unfinished_writing_interrupted(engine_setup, tmp_path):  # noqa: F811
    from store import Store
    store, cfg, *_ = engine_setup
    key = f"diary_activity:{cfg['bot_qq']}:{cfg['owner_qq']}"
    store.set(key, dict(phase='writing', day='2026-10-27', generation=0))
    path = store.db.execute('PRAGMA database_list').fetchone()[2]
    reopened = Store(path)
    try:
        assert DiaryBook(reopened.db, cfg, tmp_path).activity()['phase'] == 'interrupted'
    finally:
        reopened.db.close()
