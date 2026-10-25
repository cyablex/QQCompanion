import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import autopost
from store import Store


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "unknown"])
async def test_self_like_follows_confirmed_publication_after_send_lock_released(tmp_path, monkeypatch, status):
    store = Store(tmp_path / "self-like.sqlite3")
    lock, likes = asyncio.Lock(), []
    monkeypatch.setattr(autopost, "read_settings", lambda: {"bot_qq": "67890"})

    async def like(post):
        assert not lock.locked()
        likes.append((str(post.uin), post.tid))

    poster = autopost.AutoPosts(SimpleNamespace(store=store, engine=SimpleNamespace(send_lock=lock),
                                               social=SimpleNamespace(like_post=like)))

    async def publish(force=False):
        async with lock:
            store.set("last_own_post", dict(tid="new-post", verified=status == "succeeded"))
        return status

    monkeypatch.setattr(poster, "_run", publish)
    assert await poster.run() == status
    assert likes == ([("67890", "new-post")] if status == "succeeded" else [])
    store.db.close()
