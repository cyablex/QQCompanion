import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from conversation_memory import ConversationMemory
from creative_projects import CreativeProjects
from experience_calendar import ExperienceCalendar
from store import Store, TZ


@pytest.fixture
def calendar(tmp_path):
    store = Store(tmp_path / 'calendar.sqlite3')
    ConversationMemory(store)
    cfg = dict(owner_qq='12345', bot_qq='67890')
    yield store, cfg, ExperienceCalendar(store.db, cfg, tmp_path), tmp_path
    store.db.close()


def memory(store, contact, text, stamp, kind='diary', evidence=None, status='current'):
    store.message(contact, 'assistant', text, stamp)
    source = store.db.execute('SELECT max(id) FROM messages').fetchone()[0]
    store.db.execute(
        "INSERT INTO cm_items(contact,kind,text,source_id,evidence,status,created) VALUES(?,?,?,?,?,?,?)",
        (contact, kind, '旧的自由总结不应显示', source, evidence or text, status, stamp))


def test_calendar_uses_evidence_and_owner_scope_with_local_month_boundaries(calendar):
    store, cfg, view, _ = calendar
    edge = datetime(2026, 10, 1, tzinfo=TZ).timestamp()
    memory(store, cfg['owner_qq'], '九月最后一句', edge - 1)
    memory(store, cfg['owner_qq'], '十月一起聊的歌', edge)
    memory(store, '99999', '其他好友的秘密', edge)
    memory(store, cfg['owner_qq'], '一段真实原话', edge, evidence='不存在的虚构日记')
    memory(store, cfg['owner_qq'], '已被替代的内容', edge, status='superseded')
    changes = store.db.total_changes
    result = view.month('2026-10')
    assert [event['text'] for event in result['events']] == ['十月一起聊的歌']
    assert result['days'] == {'2026-10-01': 1}
    assert store.db.total_changes == changes
    assert len(view.month('2026-09')['events']) == 1
    assert not view.month('2026-10', '找不到')['events']


def test_saved_links_notes_and_works_survive_reopen_and_forget_clears_timeline(calendar):
    store, cfg, view, root = calendar
    projects = CreativeProjects(store.db, cfg, root)
    project = projects.create('一起看的一段视频', 'reading')
    projects.note(project['id'], '喜欢最后一句', 'companion')
    projects.link(project['id'], '片段一', 'https://www.bilibili.com/video/example?t=12', '看到12秒')
    month = datetime.now(TZ).strftime('%Y-%m')
    result = view.month(month)
    assert {event['kind'] for event in result['events']} == {'project', 'note', 'link'}
    assert all(event['project_id'] == project['id'] for event in result['events'])
    assert len(ExperienceCalendar(store.db, cfg, root).month(month)['events']) == 3
    assert len(view.month(month, '12秒')['events']) == 1
    assert not ExperienceCalendar(store.db, dict(cfg, owner_qq='99999'), root).month(month)['events']
    memory(store, cfg['owner_qq'], '可遗忘的摘记', datetime.now(TZ).timestamp())
    store.forget(cfg['owner_qq'])
    assert not view.month(month)['events']


@pytest.mark.parametrize('month', ['2026-00', '2026-13', '2026-1', '9999-12', 'not-a-month'])
def test_invalid_calendar_month_has_a_clear_error(calendar, month):
    with pytest.raises(ValueError, match='有效月份'):
        calendar[2].month(month)


def test_historical_projects_are_retrieved_without_changing_current_or_other_contact(calendar):
    store, cfg, _, root = calendar
    projects = CreativeProjects(store.db, cfg, root)
    old = projects.create('Time Machine 一起听', 'song')
    projects.link(old['id'], '歌曲讨论', 'https://example.org/song', '保留原来的副歌')
    current = projects.create('今天读的书', 'reading')
    context = projects.context(cfg['owner_qq'], 'Time Machine 上次的副歌')
    records = json.loads(context.split('\n')[-1])
    assert records['current']['id'] == current['id']
    assert records['related'][0]['id'] == old['id']
    assert projects.active()['id'] == current['id']
    assert not projects.context('99999', 'Time Machine')
    assert 'example.org/song' in context


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///C:/private', 'https://user:pass@example.org', 'https://example.org/\nopen'])
def test_shared_link_cannot_launch_local_files_or_credentials(calendar, url):
    store, cfg, _, root = calendar
    projects = CreativeProjects(store.db, cfg, root)
    project = projects.create('链接')
    with pytest.raises(ValueError):
        projects.link(project['id'], '标题', url)
    assert not projects.get(project['id']).get('links')


def test_link_retry_does_not_duplicate_shared_experience(calendar):
    store, cfg, _, root = calendar
    projects = CreativeProjects(store.db, cfg, root)
    project = projects.create('片段')
    for _ in range(2):
        projects.link(project['id'], '同一个片段', 'https://example.org/watch', '再看一次', 'same-task')
    assert len(projects.get(project['id'])['links']) == 1


def test_long_shared_histories_do_not_fill_the_chat_with_entire_archives(calendar):
    store, cfg, _, root = calendar
    projects = CreativeProjects(store.db, cfg, root)
    for index in range(5):
        project = projects.create(f'共同片段{index}', 'reading')
        for _ in range(8):
            projects.note(project['id'], '一起看的片段和感受' * 120)
    context = projects.context(cfg['owner_qq'], '一起看的片段')
    assert len(context) < 19000
    assert projects.active()['entries'][-1]['text'] == '一起看的片段和感受' * 120
