import asyncio
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import autopost
import desktop_api
import post_drafts
from post_drafts import PostDrafts
from store import Store, TZ


@pytest.fixture
def posting(tmp_path, monkeypatch):
    (tmp_path / 'data').mkdir()
    store = Store(tmp_path / 'data/companion.sqlite3')
    cfg = dict(owner_qq='12345', bot_qq='67890', revision=1, live=True, paused=False,
               autonomous_posts=True, post_image_probability=0)
    clock = [datetime(2026, 10, 29, 12, tzinfo=TZ).timestamp()]
    monkeypatch.setattr(autopost.time, 'time', lambda: clock[0])

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock[0], tz)

    monkeypatch.setattr(autopost, 'datetime', Clock)
    monkeypatch.setattr(autopost, 'read_settings', lambda: dict(cfg))
    monkeypatch.setattr(desktop_api, 'read_settings', lambda: dict(cfg))
    monkeypatch.setattr(desktop_api, 'ROOT', tmp_path)
    monkeypatch.setattr(post_drafts, 'ROOT', tmp_path)
    writes, decisions, reviews = [], [], []
    content = '把鲸鱼画进茶杯：尾巴露在外面，杯柄只好当潜望镜。'
    review = dict(worthy=True, reason='茶杯潜望镜有具体的小情节。')

    async def decision(*args, **kwargs):
        decisions.append((args, kwargs))
        return dict(action='reply', messages=[content], revision=cfg['revision'])

    async def generate(kind, system, contexts):
        reviews.append((kind, system, contexts))
        return json.dumps(review if kind == 'post_worth_sharing' else dict(novel=True))

    async def check():
        pass

    async def send(name, **kwargs):
        writes.append((name, kwargs))
        return dict(tid='test-post')

    async def feeds(*args, **kwargs):
        return SimpleNamespace(ok=True, data=dict(msglist=[dict(tid='test-post', text=content)]))

    monkeypatch.setitem(sys.modules, 'qzone_vendor.core.qzone', SimpleNamespace(
        QzoneParser=SimpleNamespace(parse_feeds=lambda rows: [SimpleNamespace(**r) for r in rows])))
    engine = SimpleNamespace(send_lock=asyncio.Lock(), decision=decision, generate_aux=generate, memory_epoch=lambda _: 0)
    plugin = SimpleNamespace(store=store, engine=engine, check_account=check,
                             bot=SimpleNamespace(call_action=send),
                             social=SimpleNamespace(ensure_qzone=check, qzone_api=SimpleNamespace(get_feeds=feeds)))
    poster = autopost.AutoPosts(plugin)
    yield SimpleNamespace(store=store, cfg=cfg, poster=poster, clock=clock, review=review,
                          writes=writes, decisions=decisions, reviews=reviews, content=content,
                          root=tmp_path, plugin=plugin)
    store.db.close()


@pytest.mark.asyncio
async def test_default_is_draft_and_approval_publishes_exactly_once_after_restart(posting):
    e = posting
    assert await e.poster.run() == 'draft_created'
    assert not e.writes
    draft = e.poster.drafts.active(e.cfg)
    assert draft['content'] == e.content
    assert e.poster.draft_command('99999', '/同意动态 ' + draft['id']) is None
    assert await e.poster.run(force=True) == 'pending_review'
    assert len(e.decisions) == 1
    assert '随机' in e.decisions[0][0][2] and '摸头' in e.decisions[0][0][2]
    assert e.content in e.poster.draft_command('12345', '/待发动态')
    assert '已批准' in e.poster.draft_command('12345', '/同意动态 ' + draft['id'], e.clock[0])
    recovered = autopost.AutoPosts(e.plugin)
    await asyncio.gather(recovered.resume(), recovered.resume())
    assert len(e.writes) == 1
    assert e.writes[0][1] == dict(content=e.content, images=[], ugc_right=4)
    assert recovered.drafts.rows(e.cfg)[0]['state'] == 'succeeded'
    await autopost.AutoPosts(e.plugin).resume()
    assert len(e.writes) == 1


@pytest.mark.asyncio
async def test_new_but_uninteresting_content_is_not_drafted_and_restart_keeps_cooldown(posting):
    e = posting
    e.review.update(worthy=False, reason='只有泛泛心情，没有具体趣味。')
    assert await e.poster.run() == 'not_worth_sharing'
    assert not e.poster.drafts.rows(e.cfg) and not e.writes
    # Cross a date boundary; neither a fresh instance nor an art trigger resets it.
    e.clock[0] += 24 * 3600
    e.store.set('own_post_requested', True)
    assert await autopost.AutoPosts(e.plugin).run() == 'waiting'
    assert len(e.decisions) == 1
    e.clock[0] += 24 * 3600
    assert await e.poster.run() == 'not_worth_sharing'
    assert len(e.decisions) == 2


@pytest.mark.asyncio
async def test_rejection_and_expiration_never_publish_or_immediately_replace_draft(posting):
    e = posting
    assert await e.poster.run() == 'draft_created'
    row = e.poster.drafts.active(e.cfg)
    e.poster.draft_command('12345', '/拒绝动态 ' + row['id'], e.clock[0])
    await e.poster.resume()
    assert await e.poster.run() == 'waiting'
    e.clock[0] += 48 * 3600
    assert await e.poster.run() == 'duplicate_content'  # Refused ideas are not recycled.
    e.poster.drafts.create(e.cfg, '另一个独立的新点子。', {})
    row = e.poster.drafts.active(e.cfg)
    e.clock[0] += 73 * 3600
    assert '过期' in e.poster.draft_command('12345', '/同意动态 ' + row['id'], e.clock[0])
    await e.poster.resume()
    assert not e.writes


@pytest.mark.asyncio
async def test_approved_draft_waits_through_pause_and_quiet_hours(posting):
    e = posting
    await e.poster.run()
    row = e.poster.drafts.active(e.cfg)
    e.poster.drafts.decide(e.cfg, row['id'], True, row['content'])
    e.cfg['paused'] = True
    await e.poster.resume()
    e.cfg['paused'] = False
    e.clock[0] += 13 * 3600  # 01:00 Beijing
    await e.poster.resume()
    assert not e.writes
    e.clock[0] += 8 * 3600
    await e.poster.resume()
    assert len(e.writes) == 1


@pytest.mark.asyncio
async def test_unknown_send_is_not_replayed_after_restart_or_approval(posting):
    e = posting
    await e.poster.run()
    row = e.poster.drafts.active(e.cfg)
    e.poster.drafts.decide(e.cfg, row['id'], True, row['content'])

    async def timeout(*args, **kwargs):
        e.writes.append('attempt')
        raise TimeoutError()

    e.plugin.bot.call_action = timeout
    with pytest.raises(TimeoutError):
        await e.poster.resume()
    await autopost.AutoPosts(e.plugin).resume()
    assert e.writes == ['attempt']
    assert e.poster.drafts.rows(e.cfg)[0]['state'] == 'unknown'
    assert '已处理' in e.poster.draft_command('12345', '/同意动态 ' + row['id'], e.clock[0])


@pytest.mark.asyncio
async def test_owner_share_revocation_cancels_approved_draft(posting):
    e = posting
    token = e.poster.drafts.create(e.cfg, e.content, dict(owner_share=dict(generation=0)))
    e.poster.drafts.decide(e.cfg, token, True, e.content)
    await e.poster.resume()
    assert e.poster.drafts.rows(e.cfg)[0]['state'] == 'cancelled'
    assert not e.writes


@pytest.mark.asyncio
async def test_revocation_while_connecting_prevents_send(posting):
    e = posting
    await e.poster.run()
    row = e.poster.drafts.active(e.cfg)
    e.poster.drafts.decide(e.cfg, row['id'], True, row['content'])

    async def revoke():
        e.poster.drafts.decide(e.cfg, row['id'], False, row['content'])

    e.plugin.check_account = revoke
    await e.poster.resume()
    assert not e.writes
    assert e.poster.drafts.rows(e.cfg)[0]['state'] == 'rejected'


@pytest.mark.asyncio
async def test_restarting_after_sending_started_does_not_repeat_external_write(posting):
    e = posting
    token = e.poster.drafts.create(e.cfg, e.content, {})
    e.poster.drafts.finish(token, 'sending', 'Interrupted')
    key = f"qzone_post:{e.cfg['bot_qq']}:draft:{token}"
    e.store.action(key, 'qzone:' + e.cfg['bot_qq'], 'qzone_post', [e.content])
    e.store.outcome(key, 'unknown')
    recovered = autopost.AutoPosts(e.plugin)
    await recovered.resume()
    assert not e.writes and recovered.drafts.rows(e.cfg)[0]['state'] == 'unknown'


@pytest.mark.asyncio
async def test_bad_review_is_not_treated_as_permission_to_post(posting):
    e = posting
    e.review.clear()
    with pytest.raises(ValueError):
        await e.poster.run()
    assert not e.poster.drafts.rows(e.cfg) and not e.writes


@pytest.mark.asyncio
async def test_opted_in_automatic_mode_still_reviews_and_waits_48_hours(posting):
    e = posting
    e.cfg['post_requires_approval'] = False
    assert await e.poster.run() == 'succeeded'
    e.clock[0] += 24 * 3600
    assert await e.poster.run() == 'waiting'
    assert len(e.writes) == 1 and len(e.decisions) == 1


def test_console_previews_image_and_cannot_approve_changed_content_or_other_account(posting):
    e = posting
    image = e.root / 'source.png'
    Image.new('RGB', (8, 8), 'blue').save(image)
    token = e.poster.drafts.create(e.cfg, e.content, dict(image=dict(file=str(image))))
    image.unlink()  # A draft owns a snapshot, independent of the source gallery.
    api = desktop_api.DesktopAPI()
    preview = api.get_post_drafts()['data'][0]
    assert preview['image'].startswith('data:image/png;base64,')
    assert not api.decide_post_draft(token, True, e.content + '改稿')['ok']
    e.cfg['bot_qq'] = '88888'
    assert api.get_post_drafts()['data'] == []
    assert not api.decide_post_draft(token, True, e.content)['ok']
    e.cfg['bot_qq'] = '67890'
    assert api.decide_post_draft(token, True, e.content)['ok']
    assert not api.decide_post_draft(token, True, e.content)['ok']
    with sqlite3.connect(e.root / 'data/companion.sqlite3') as db:
        db.row_factory = sqlite3.Row
        assert PostDrafts(db).rows(e.cfg)[0]['state'] == 'approved'
