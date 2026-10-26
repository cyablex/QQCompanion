import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from creative_projects import CreativeProjects
from song_previews import SongPreviews
from store import Store


@pytest.fixture
def room(tmp_path):
    store = Store(tmp_path / 'data.sqlite3')
    cfg = dict(bot_qq='67890', owner_qq='12345')
    yield store, cfg, CreativeProjects(store.db, cfg, tmp_path), tmp_path
    store.db.close()


def test_project_reopens_with_real_work_note_and_next_step(room):
    store, cfg, projects, root = room
    song = projects.available_assets()
    assert song == []
    from song_library import SongLibrary

    library = SongLibrary(store.db, root)
    track = library.add('time machine', 'mj apanay')
    project = projects.create('把昨天的歌接着做')
    projects.attach(project['id'], 'song', track['id'])
    projects.note(project['id'], '低声区还想再柔和一点')
    projects.update(project['id'], 'adjust', '先比较前30秒，再决定采用哪版')
    reopened = CreativeProjects(store.db, cfg, root)
    assert reopened.active()['next_step'] == '先比较前30秒，再决定采用哪版'
    assert reopened.view(reopened.active())['assets'][0]['state'] == 'queued'
    context = reopened.context('12345')
    assert '低声区还想再柔和一点' in context and 'time machine' in context
    assert str(root) not in context and not reopened.context('99999')


def test_private_project_and_other_contacts_art_never_cross_scope(room):
    store, cfg, projects, root = room
    project = projects.create('仅主人可见')
    projects.note(project['id'], '私有的想法')
    assert not CreativeProjects(store.db, dict(cfg, owner_qq='99999'), root).list()
    assert not CreativeProjects(store.db, dict(cfg, bot_qq='99999'), root).list()
    ident = 'a' * 32
    file = root / 'data/drawings/67890/99999' / (ident + '.png')
    file.parent.mkdir(parents=True)
    Image.new('RGB', (4, 4)).save(file)
    store.set('sketches:67890:99999', [dict(id=ident, file=str(file), title='其他好友的画')])
    assert not projects.available_assets()
    with pytest.raises(ValueError):
        projects.attach(project['id'], 'drawing', ident, '99999')
    store.forget('12345')
    assert not projects.list() and projects.active() is None


def test_note_retry_is_idempotent_and_missing_asset_does_not_invent_work(room):
    _, _, projects, _ = room
    project = projects.create('一个点子')
    projects.note(project['id'], '同一个提议', 'companion', 'same-event')
    projects.note(project['id'], '同一个提议', 'companion', 'same-event')
    assert len(projects.get(project['id'])['entries']) == 1
    with pytest.raises(ValueError):
        projects.attach(project['id'], 'song', 'f' * 32)
    assert not projects.get(project['id'])['assets']


def test_adopting_preview_keeps_previous_finished_audio(room):
    store, _, _, root = room
    from song_library import SongLibrary

    library = SongLibrary(store.db, root)
    song = library.add('歌', '版本')
    target = library.audio_path(song['id']).resolve()
    target.parent.mkdir(parents=True)
    target.write_bytes(b'original-finished-audio')
    store.db.execute("UPDATE songs SET state='ready',file=?,duration=60 WHERE id=?", (str(target), song['id']))
    previews = SongPreviews(store.db, root)
    version = 'b' * 32
    folder = previews.folder(song['id'], version)
    folder.mkdir(parents=True)
    new = folder / 'song.mp3'
    new.write_bytes(b'new-finished-audio')
    (folder / 'job.json').write_text(json.dumps(dict(label='调整版')), encoding='utf-8')
    (folder / 'result.json').write_text(json.dumps(dict(state='ready', file=str(new.resolve()), duration=60)), encoding='utf-8')
    saved = previews.adopt(song['id'], version)
    assert target.read_bytes() == b'new-finished-audio'
    old, _ = previews.ready_file(song['id'], saved['previous'])
    assert old.read_bytes() == b'original-finished-audio'
    assert len(previews.list(song['id'])) == 2
    with pytest.raises(ValueError):
        previews.ready_file(song['id'], '../outside')
