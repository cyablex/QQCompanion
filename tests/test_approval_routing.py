# ruff: noqa: E402
"""Exercise approval routing with fake models/transports and temporary data."""

import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'astrbot'))
import autopost
import offline_recovery
from approval_commands import parse_approval_command
from settings import atomic_json
from vpet_bridge import VPetBridge
from test_post_drafts import posting as posting
from test_turn_actions import turns as turns, task, incoming, state, offer
from test_singing import songs as songs
from test_offline_recovery import env as env, msg


@pytest.mark.parametrize('text,domain,approve,token', [
    ('同意动态 abcde123', 'post', True, 'abcde123'),
    ('/同意动态 abcde123', 'post', True, 'abcde123'),
    ('／同意动态\tABCDE123！', 'post', True, 'abcde123'),
    ('同意动态ABCDE123', 'post', True, 'abcde123'),
    (' 拒绝动态\nabcde123。 ', 'post', False, 'abcde123'),
    ('同意评论 ABCDEF', 'comment', True, 'abcdef'),
    ('／拒绝评论 abcde123', 'comment', False, 'abcde123'),
    ('待发动态', 'post', None, ''), ('/动态草稿', 'post', None, ''),
    ('待审批。', 'comment', None, ''),
])
def test_named_commands_have_one_domain_without_model(text, domain, approve, token):
    parsed = parse_approval_command(text)
    assert (parsed.domain, parsed.approve, parsed.token, parsed.error) == (domain, approve, token, '')


@pytest.mark.parametrize('text', ['同意动态', '/同意动态 nope', '同意动态 abc123',
                                 '同意动态 abcde123，但先别发', '拒绝评论 xyz'])
def test_malformed_command_has_no_executable_token(text):
    parsed = parse_approval_command(text)
    assert parsed.error and not parsed.token


@pytest.mark.parametrize('text', ['“同意动态 abcde123”', '不要同意动态 abcde123',
                                 '同意动态是什么意思？', '待发动态怎么用',
                                 '他说：同意评论 abcde123', '我同意这个说法', '今天过得怎样'])
def test_quotes_questions_and_normal_chat_are_not_commands(text):
    assert parse_approval_command(text) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('variant', ['plain', 'slash', 'fullwidth', 'bad', 'comment'])
async def test_actual_receive_routes_named_approval_without_model(posting, monkeypatch, variant):
    e = posting
    monkeypatch.setenv('QQCOMPANION_ROOT', str(e.root))
    spec = importlib.util.spec_from_file_location('approval_receive_test', ROOT / 'plugins/astrbot_plugin_qq_companion/main.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'read_settings', lambda: dict(e.cfg))
    token = e.poster.drafts.create(e.cfg, e.content, {})
    replies, comments = [], []

    async def send(contact, text):
        replies.append(text)

    async def comment(contact, text, **kwargs):
        comments.append(text)
        return '评论已收到'

    plugin = module.QQCompanion.__new__(module.QQCompanion)
    plugin.store, plugin.autopost = e.store, e.poster
    plugin.engine = SimpleNamespace(config_reader=lambda: e.cfg)
    plugin.send_private = send
    plugin.social = SimpleNamespace(approval_command=comment)
    text = {'plain': '同意动态 ' + token, 'slash': '/同意动态 ' + token,
            'fullwidth': '／同意动态 ' + token.upper() + '。', 'bad': '同意动态 xyz',
            'comment': '同意评论 ABCDE123'}[variant]
    raw = dict(self_id=67890, post_type='message', message_type='private', user_id=12345,
               message_id=10, time=int(e.clock[0]), message=[dict(type='text', data=dict(text=text))])
    event = SimpleNamespace(message_obj=SimpleNamespace(raw_message=raw), bot=object(),
                            message_str=text, stop_event=lambda: None)
    # There is intentionally no generate/enqueue_reply: a fallback to the model
    # would fail the real handler test, rather than silently pass a parser test.
    await plugin.receive(event)
    assert e.poster.drafts.rows(e.cfg)[0]['state'] == ('approved' if variant in {'plain', 'slash', 'fullwidth'} else 'pending')
    assert len(replies) == 1
    assert comments == (['/同意评论 abcde123'] if variant == 'comment' else [])
    assert not e.writes
    if variant in {'plain', 'slash', 'fullwidth'}:
        await e.poster.resume()
        assert len(e.writes) == 1 and e.poster.drafts.rows(e.cfg)[0]['state'] == 'succeeded'
        await plugin.receive(event)
        await e.poster.resume()
        assert len(e.writes) == 1


def displayed(e, drafts, token):
    text = f'动态草稿待确认 [{token}]\n{e.content}\n\n同意：/同意动态 {token}'
    e.store.action('post_draft_request:' + token, e.cfg['owner_qq'], 'approval_request', [text])
    e.store.outcome('post_draft_request:' + token, 'succeeded')


def attach_poster(e, monkeypatch):
    monkeypatch.setattr(autopost, 'read_settings', lambda: e.cfg)
    e.cfg.update(autonomous_posts=True)
    e.plugin.autopost = autopost.AutoPosts(e.plugin)
    e.content = '把一颗糖画成小星球。'
    token = e.plugin.autopost.drafts.create(e.cfg, e.content, {})
    displayed(e, e.plugin.autopost.drafts, token)
    return token


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix', ['', '/', '／'])
async def test_desktop_chat_uses_same_command_router_and_does_not_enqueue_model(turns, monkeypatch, prefix):
    e = turns
    token = attach_poster(e, monkeypatch)
    atomic_json(e.root / 'config/vpet.json', dict(enabled=True))
    bridge = VPetBridge(e.plugin, root=e.root)
    request = dict(id='a' * 32, text=prefix + '同意动态 ' + token)
    result = await bridge.chat(request)
    assert result['accepted'] and '已批准' in result['confirmation']
    assert await bridge.chat(request) == result
    assert e.plugin.autopost.drafts.rows(e.cfg)[0]['state'] == 'approved'
    assert not e.calls and not e.sent and not e.writes


@pytest.mark.asyncio
async def test_natural_post_approval_and_status_use_real_draft_and_receipt(turns, monkeypatch):
    e = turns
    token = attach_poster(e, monkeypatch)
    text = '这个糖果动态就按原稿发吧'
    incoming(e, text)
    task(e, 'post.approve', token=token, evidence=text)
    e.plan['messages'] = ['那颗糖就交出去啦，配图都照刚才的来。']
    await e.engine.reply('12345')
    assert state(e) == 'waiting'
    assert len(e.sent) == 1 and '已批准，等待' in e.sent[0][1]
    assert not e.writes and not e.store.db.execute('SELECT 1 FROM qzone_authorizations').fetchone()
    assert 'post.approve' in e.calls[0][0] and token in e.calls[0][0]
    e.plugin.autopost.drafts.finish(token, 'succeeded', '已按批准的原文发布，并读回确认。')
    e.router.sync()
    assert state(e) == 'succeeded'
    incoming(e, '发出去了吗', event='check-status')
    task(e, 'post.status')
    await e.engine.reply('12345')
    assert '读回确认' in e.sent[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['evidence', 'changed', 'unshown', 'expired', 'account', 'comment_id'])
async def test_natural_post_approval_never_substitutes_unverified_target(turns, monkeypatch, problem):
    e = turns
    token = attach_poster(e, monkeypatch)
    if problem == 'comment_id':
        token = (await offer(e))[0]
    if problem == 'changed':
        e.store.db.execute("UPDATE qzone_post_drafts SET content='已经改稿'")
    if problem == 'unshown':
        e.store.db.execute("DELETE FROM actions WHERE id LIKE 'post_draft_request:%'")
    if problem == 'expired':
        e.store.db.execute('UPDATE qzone_post_drafts SET expires=?', (time.time() - 10,))
    if problem == 'account':
        e.store.db.execute("UPDATE qzone_post_drafts SET bot='77777'")
    e.sent.clear()
    incoming(e, '按原稿发动态吧')
    task(e, 'post.approve', token=token, evidence='其他人的话' if problem == 'evidence' else '按原稿发动态吧')
    await e.engine.reply('12345')
    assert state(e) == 'rejected'
    assert not e.writes and not e.store.db.execute('SELECT 1 FROM qzone_post_drafts WHERE state=\'approved\'').fetchone()


@pytest.mark.asyncio
async def test_wrong_model_comment_action_cannot_consume_post_approval_or_claim_success(turns, monkeypatch):
    e = turns
    token = attach_poster(e, monkeypatch)
    comments = await offer(e)
    e.sent.clear()
    incoming(e, '同意动态 ' + token)
    task(e, 'comment.approve', tokens=comments, evidence='同意动态 ' + token)
    e.plan['messages'] = ['那颗糖就交出去啦。']
    await e.engine.reply('12345')
    assert state(e) == 'rejected' and len(e.sent) == 1
    assert '不对应评论审批' in e.sent[0][1] and '就交出去' not in e.sent[0][1]
    assert not e.store.db.execute('SELECT 1 FROM qzone_authorizations').fetchone()


@pytest.mark.asyncio
@pytest.mark.parametrize('approved', [False, True])
async def test_natural_refusal_revokes_only_current_draft_and_retries_do_not_repeat(turns, monkeypatch, approved):
    e = turns
    token = attach_poster(e, monkeypatch)
    if approved:
        e.plugin.autopost.drafts.decide(e.cfg, token, True, e.content)
    incoming(e, '这个动态先不发了')
    task(e, 'post.reject', token=token, evidence='这个动态先不发了')
    await e.engine.reply('12345')
    assert e.plugin.autopost.drafts.rows(e.cfg)[0]['state'] == 'rejected'
    assert state(e) == 'succeeded' and len(e.sent) == 1
    await e.engine.reply('12345')
    assert len(e.sent) == 1 and not e.writes


@pytest.mark.asyncio
@pytest.mark.parametrize('contact', ['11111', 'group:99999'])
async def test_model_cannot_supply_owner_authority_from_friend_or_group(turns, monkeypatch, contact):
    e = turns
    token = attach_poster(e, monkeypatch)
    incoming(e, '同意动态 ' + token, contact=contact)
    task(e, 'post.approve', token=token, evidence='同意动态 ' + token)
    engine = e.groups.engine if contact.startswith('group:') else e.engine
    await engine.reply(contact)
    assert e.plugin.autopost.drafts.rows(e.cfg)[0]['state'] == 'pending'
    assert not e.writes


@pytest.mark.parametrize('problem', ['', 'later_refusal', 'unshown', 'expired', 'forgotten', 'other_sender', 'changed'])
def test_recovery_keeps_real_approval_and_later_refusal_without_publishing(posting, problem):
    e = posting
    token = e.poster.drafts.create(e.cfg, e.content, {})
    displayed(e, e.poster.drafts, token)
    raw = dict(time=int(e.clock[0]), message_id=10, sender=dict(user_id=12345),
               message=[dict(type='text', data=dict(text='同意动态 ' + token))])
    history = [raw]
    if problem == 'later_refusal':
        history.append(dict(raw, message_id=11, time=raw['time'] + 1,
                            message=[dict(type='text', data=dict(text='拒绝动态 ' + token))]))
        e.clock[0] += 2
    if problem == 'unshown':
        e.store.db.execute("DELETE FROM actions")
    if problem == 'expired':
        e.clock[0] += 73 * 3600
    if problem == 'forgotten':
        e.store.set('offline_ignore_before:12345', raw['time'] + 1)
    if problem == 'other_sender':
        raw['sender']['user_id'] = 11111
    if problem == 'changed':
        e.store.db.execute("UPDATE qzone_post_drafts SET content='改稿'")
    e.poster.recover_approvals(history)
    e.poster.recover_approvals(history)
    expected = 'approved' if not problem else 'rejected' if problem == 'later_refusal' else 'expired' if problem == 'expired' else 'pending'
    assert e.poster.drafts.rows(e.cfg)[0]['state'] == expected
    assert not e.writes


@pytest.mark.asyncio
async def test_offline_post_command_recovers_without_chat_queue_even_after_model_ack(env, monkeypatch):
    cfg, store, plugin, histories, calls, queued, sent = env
    cfg['offline_recovery'] = False
    monkeypatch.setattr(autopost, 'read_settings', lambda: cfg)
    plugin.autopost = autopost.AutoPosts(plugin)
    token = plugin.autopost.drafts.create(cfg, '测试动态', {})
    store.db.execute('UPDATE qzone_post_drafts SET created=?', (time.time() - 120,))
    notice = f'动态草稿待确认 [{token}]\n测试动态\n\n同意：/同意动态 {token}'
    store.action('post_draft_request:' + token, '12345', 'approval_request', [notice])
    store.outcome('post_draft_request:' + token, 'succeeded')
    store.db.execute('UPDATE actions SET created=?', (time.time() - 110,))
    histories['12345'] = [msg('67890', 1, notice, age=110),
                          msg('12345', 2, '同意动态 ' + token, age=50),
                          msg('67890', 3, '正在等发布回执', age=40)]
    report = await offline_recovery.OfflineRecovery(plugin).run()
    assert report['errors'] == 0 and report['queued'] == 0
    assert plugin.autopost.drafts.rows(cfg)[0]['state'] == 'approved'
    assert not queued and not sent
