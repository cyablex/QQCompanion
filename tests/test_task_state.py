"""Task-state revisions catch direct SQL completions without writing on reads."""
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from conversation_memory import ConversationMemory  # noqa: E402
from store import Store  # noqa: E402
from task_state import unchanged, version  # noqa: E402


@pytest.fixture
def state(tmp_path):
    path = tmp_path / 'task-state.sqlite3'
    store = Store(path)
    yield store, path
    store.db.close()


def turn(store, ident='task', contact='12345', state='running'):
    store.db.execute("INSERT INTO turn_tasks(id,contact,kind,state,result,owner,bot,generation,updated) "
                     "VALUES(?,?,'drawing.create',?,'{}','12345','67890',0,1)", (ident, contact, state))


def song(store):
    store.db.execute("INSERT INTO songs(id,title,artist,state,progress,file) VALUES('song','歌','版本','processing','分离中','')")


def request(store, ident='request', contact='12345'):
    store.db.execute("INSERT INTO song_requests(id,song_id,contact,requester,owner,bot,state,result,generation,updated) "
                     "VALUES(?,'song',?,?,'12345','67890','waiting','',0,1)", (ident, contact, contact))


def test_task_completion_invalidates_only_its_contact_and_ignores_identical_sync(state):
    store, _ = state
    assert version(store, '12345') == version(store, '99999') == 0
    turn(store)
    captured = version(store, '12345')
    store.db.execute("UPDATE turn_tasks SET state='running',result='{}',updated=2 WHERE id='task'")
    assert unchanged(store, '12345', captured)
    store.db.execute("UPDATE turn_tasks SET state='succeeded',result='确认发送',updated=3 WHERE id='task'")
    assert not unchanged(store, '12345', captured)
    assert version(store, '99999') == 0


def test_background_song_and_delivery_changes_invalidate_but_polling_does_not(state):
    store, _ = state
    song(store)
    request(store)
    request(store, ident='guest-request', contact='99999')
    captured = version(store, '12345')
    guest_captured = version(store, '99999')
    store.db.execute("UPDATE songs SET progress='转换中',updated=10,worker_pid=123 WHERE id='song'")
    store.db.execute("UPDATE song_requests SET due=30,updated=11 WHERE id='request'")
    assert unchanged(store, '12345', captured)
    store.db.execute("UPDATE songs SET state='ready',file='song.mp3',progress='完成',updated=12 WHERE id='song'")
    assert not unchanged(store, '12345', captured)
    assert not unchanged(store, '99999', guest_captured)
    captured, guest_captured = version(store, '12345'), version(store, '99999')
    store.db.execute("UPDATE song_requests SET state='sent',result='QQ已确认接收歌曲语音。' WHERE id='request'")
    assert not unchanged(store, '12345', captured)
    assert unchanged(store, '99999', guest_captured)


def test_task_removal_and_forget_do_not_reset_the_phase_revision(state):
    store, _ = state
    memory = ConversationMemory(store)
    turn(store)
    before = version(store, '12345')
    generation = memory.generation('12345')
    store.forget('12345')
    assert version(store, '12345') > before
    assert memory.generation('12345') > generation
    captured = version(store, '12345')
    store.db.execute("DELETE FROM turn_tasks WHERE id='task'")
    assert version(store, '12345') > captured


def test_epoch_reads_are_read_only_and_survive_reopening(state):
    store, path = state
    turn(store)
    expected = version(store, '12345')
    count = store.db.total_changes
    assert version(store, 'unseen') == 0
    assert unchanged(store, '12345', expected)
    assert store.db.total_changes == count
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        readonly = SimpleNamespace(db=db)
        assert version(readonly, '12345') == expected
        assert version(readonly, 'unseen') == 0
    reopened = Store(path, recover=False)
    try:
        assert version(reopened, '12345') == expected
        reopened.db.execute("UPDATE turn_tasks SET state='succeeded' WHERE id='task'")
        assert not unchanged(store, '12345', expected)
    finally:
        reopened.db.close()


def test_contact_transfer_invalidates_both_old_and_new_views(state):
    store, _ = state
    turn(store)
    captured = version(store, '12345')
    store.db.execute("UPDATE turn_tasks SET contact='99999' WHERE id='task'")
    assert not unchanged(store, '12345', captured)
    assert version(store, '99999') > 0
