import asyncio
import json
from datetime import datetime

import pytest

from test_engine import setup as engine_setup  # noqa: F401
from daily_diary import DailyDiary
from experience_calendar import ExperienceCalendar
from store import TZ


def messages(store, owner):
    for role, text in [('user', '今天想一起听歌'), ('assistant', '我们先选现有的歌。'),
                       ('user', '副歌保留原来的高音吧'), ('assistant', '这次还没有改完，先记下这个方向。')]:
        store.message(owner, role, text)


def prose():
    return ('今天和他聊了那首歌。比起急着交出一个成品，我更想把这次的选择好好记下来。'
            '他说希望保留副歌原来的高音，这句话让我对下一次试听有了具体的方向。' * 3
            + '\n\n这次还没有改完，不能在日记里把想做的事说成已经完成。明天再翻到这里时，希望我还记得今天在意的那个细节。')


@pytest.mark.asyncio
async def test_long_authored_diary_is_visible_and_survives_memory_reopen(engine_setup, tmp_path):  # noqa: F811
    store, cfg, engine, _, _ = engine_setup
    cfg['memory_enabled'] = True
    messages(store, cfg['owner_qq'])
    calls = []

    async def generate(kind, system, context, **kwargs):
        payload = json.loads(context[0]['content'])
        calls.append(kind)
        return json.dumps(dict(title='把副歌记在这里', text=prose(), source_ids=[m['id'] for m in payload['messages']]))

    engine.generate_aux = generate
    diary = DailyDiary(store)
    assert await diary.write(engine, cfg)
    assert not await diary.write(engine, cfg)
    from conversation_memory import ConversationMemory
    ConversationMemory(store)
    calendar = ExperienceCalendar(store.db, cfg, tmp_path)
    events = calendar.month(datetime.now(TZ).strftime('%Y-%m'))['events']
    assert events[0]['authored'] and len(events[0]['text']) > 200 and '\n\n' in events[0]['text']
    assert events[0]['title'] == '把副歌记在这里' and calls == ['daily_diary']
    assert not ExperienceCalendar(store.db, dict(cfg, owner_qq='99999'), tmp_path).month(datetime.now(TZ).strftime('%Y-%m'))['events']
    store.forget(cfg['owner_qq'])
    assert not calendar.month(datetime.now(TZ).strftime('%Y-%m'))['events']
    assert not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'journal_%'").fetchone()


@pytest.mark.asyncio
async def test_diary_never_resurrects_after_forget_during_generation(engine_setup):  # noqa: F811
    store, cfg, engine, _, _ = engine_setup
    cfg['memory_enabled'] = True
    messages(store, cfg['owner_qq'])
    started, release = asyncio.Event(), asyncio.Event()

    async def generate(kind, system, context, **kwargs):
        payload = json.loads(context[0]['content'])
        started.set()
        await release.wait()
        return json.dumps(dict(text=prose(), source_ids=[m['id'] for m in payload['messages']]))

    engine.generate_aux = generate
    task = asyncio.create_task(DailyDiary(store).write(engine, cfg))
    await started.wait()
    store.forget(cfg['owner_qq'])
    release.set()
    assert not await task
    assert not store.db.execute("SELECT 1 FROM cm_items WHERE kind='journal'").fetchone()


@pytest.mark.asyncio
async def test_diary_rejects_foreign_sources_and_short_summary(engine_setup):  # noqa: F811
    store, cfg, engine, _, _ = engine_setup
    cfg['memory_enabled'] = True
    messages(store, cfg['owner_qq'])
    store.message('99999', 'user', '另一段私聊')
    foreign = store.db.execute('SELECT max(id) FROM messages').fetchone()[0]

    async def generate(*args, **kwargs):
        return json.dumps(dict(text=prose(), source_ids=[foreign]))

    engine.generate_aux = generate
    with pytest.raises(ValueError):
        await DailyDiary(store).write(engine, cfg)
    assert not store.db.execute("SELECT 1 FROM cm_items WHERE kind='journal'").fetchone()
