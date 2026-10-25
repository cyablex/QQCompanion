import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from reply_queue import ReplyQueue
from store import Store


def test_retry_survives_restart_and_expires(tmp_path):
    path = tmp_path / "queue.sqlite3"
    now = time.time()
    store = Store(path)
    queue = ReplyQueue(store)
    queue.enqueue("12345", "one", now)
    assert queue.begin("12345")["attempts"] == 1
    queue.retry("12345", "one", now)
    store.db.close()
    store = Store(path)
    queue = ReplyQueue(store)
    assert queue.ready(now + 30) == []
    assert queue.ready(now + 61) == ["12345"]
    assert queue.ready(now + 901) == []
    store.db.close()


def test_old_worker_cannot_remove_new_message_or_retry_forever(tmp_path):
    store = Store(tmp_path / "queue.sqlite3")
    queue = ReplyQueue(store)
    queue.enqueue("12345", "old")
    queue.begin("12345")
    queue.enqueue("12345", "new")
    queue.finish("12345", "old")
    queue.retry("12345", "old")
    assert store.get("reply_job:12345")["event"] == "new"
    for attempt in range(3):
        assert queue.begin("12345")["attempts"] == attempt + 1
        queue.retry("12345", "new")
    assert store.get("reply_job:12345") is None
    store.db.close()
