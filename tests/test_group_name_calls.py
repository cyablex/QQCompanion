"""Direct name calls are replies; references remain optional interjections."""

import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from group_chat import GroupChats, called_by_name  # noqa: E402
from test_qq_faces_groups import groups as groups  # noqa: E402,F401
from test_group_recovery import clock as clock, APIConnectionError  # noqa: E402,F401
from test_turn_actions import turns as turns  # noqa: E402,F401
from test_singing import songs as songs  # noqa: E402,F401


@pytest.mark.parametrize('text', [
    '大肥鱼 好久不见', '大肥鱼，好久不见', '大肥鱼你好', '大肥鱼你在吗',
    '大肥鱼', '大肥鱼！', ' 大肥鱼\n晚上好 ', '你好，大肥鱼',
    '在吗，大肥鱼？', '大肥鱼给我唱首歌', '大肥鱼，今天过得怎么样？',
])
def test_clear_address_and_greeting_are_direct_calls(text):
    assert called_by_name(text, '大肥鱼')


@pytest.mark.parametrize('text', [
    '大肥鱼刚发了动态', '大肥鱼今天发了动态', '大肥鱼 是机器人吗',
    '你们觉得大肥鱼怎么样', '我喜欢大肥鱼', '那是大肥鱼吧',
    '有人说大肥鱼你好', '“大肥鱼你在吗”', '他说：“大肥鱼 好久不见”',
    '「大肥鱼你好」', '```大肥鱼你好```', '`大肥鱼在吗`',
    'https://example.com/大肥鱼你好', '你好',
])
def test_reference_quote_and_unrelated_text_are_not_direct_calls(text):
    assert not called_by_name(text, '大肥鱼')


def test_name_comes_from_current_configuration_and_is_not_regex():
    assert called_by_name('fish 在吗', 'fish')
    assert not called_by_name('fish 刚发了动态', 'fish')
    assert not called_by_name('大肥鱼你好', 'fish')
    assert called_by_name('fish+你好', 'fish+')
    assert not called_by_name('fish你好', 'fish+')
    assert not called_by_name('你好', '')


def message(text, ident=1, sender=33333, **extra):
    return dict(group_id=900000003, user_id=sender, message_id=ident,
                message=[dict(type='text', data=dict(text=text))], **extra)


async def drain(chats):
    await asyncio.gather(*list(chats.tasks.values()))


@pytest.mark.asyncio
async def test_name_call_bypasses_interjection_cooldown_and_daily_cap(groups, clock):
    chats, store, cfg, calls, sent = groups
    cfg.update(name='大肥鱼', group_interjections=False)
    contact = 'group:900000003'
    store.set('group_last_reply:' + contact, time.time())
    store.set('group_last_assessment:' + contact, time.time())
    for n in range(2):
        store.action('group_interjection:' + str(n), contact, 'reply', ['旧插话'])
        store.outcome('group_interjection:' + str(n), 'succeeded')
    await chats.receive(message('大肥鱼 好久不见'))
    await drain(chats)
    assert len(sent) == 1 and len(calls) == 1
    assert sent[0][0] == 'send_group_msg' and sent[0][1]['group_id'] == 900000003
    origin = store.get('song_origin:' + contact)
    assert origin['trigger'] == 'name' and origin['requester'] == '33333'
    assert origin['text'] == '大肥鱼 好久不见'
    assert not store.get('reply_job:' + contact)


@pytest.mark.asyncio
async def test_discussion_of_character_keeps_optional_path(groups, clock):
    chats, store, cfg, calls, sent = groups
    cfg['name'] = '大肥鱼'
    store.set('group_last_reply:group:900000003', time.time())
    await chats.receive(message('大肥鱼今天发了动态'))
    await drain(chats)
    assert store.history('group:900000003')
    assert not store.get('song_origin:group:900000003')
    assert not store.get('reply_job:group:900000003')
    assert not calls and not sent


@pytest.mark.asyncio
async def test_explicit_other_addressee_and_attachment_metadata_do_not_call_bot(groups, clock):
    chats, store, cfg, calls, sent = groups
    cfg['name'] = '大肥鱼'
    raw = message('大肥鱼你好')
    raw['message'].insert(0, dict(type='at', data=dict(qq='44444')))
    await chats.receive(raw)
    await drain(chats)
    raw = message('', ident=2)
    raw['message'] = [dict(type='image', data=dict(file='大肥鱼你好')),
                      dict(type='reply', data=dict(id='quoted-message', text='大肥鱼你好'))]
    await chats.receive(raw)
    await drain(chats)
    assert not store.get('song_origin:group:900000003')
    assert not calls and not sent


@pytest.mark.asyncio
async def test_real_at_still_works_when_body_only_quotes_name(groups, clock):
    chats, store, cfg, calls, sent = groups
    cfg['name'] = '大肥鱼'
    raw = message('“大肥鱼好久不见”')
    raw['message'].append(dict(type='at', data=dict(qq='22222')))
    await chats.receive(raw)
    await drain(chats)
    assert len(sent) == 1
    assert store.get('song_origin:group:900000003')['trigger'] == 'at'


@pytest.mark.asyncio
async def test_continuous_calls_coalesce_and_keep_latest_caller(groups, clock):
    chats, store, cfg, calls, sent = groups
    cfg['name'] = '大肥鱼'
    await chats.receive(message('大肥鱼'))
    await chats.receive(message('大肥鱼你在吗', ident=2, sender=44444))
    await drain(chats)
    assert len(sent) == len(calls) == 1
    origin = store.get('song_origin:group:900000003')
    assert origin['requester'] == '44444' and origin['event_id'].endswith(':2')
    # A transport duplicate cannot produce another reply.
    await chats.receive(message('大肥鱼你在吗', ident=2, sender=44444))
    await drain(chats)
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_unaddressed_chatter_does_not_replace_called_source(groups, clock):
    chats, store, cfg, calls, sent = groups
    cfg['name'] = '大肥鱼'
    await chats.receive(message('大肥鱼 好久不见'))
    await chats.receive(message('我们聊别的事', ident=2, sender=44444))
    await drain(chats)
    assert len(sent) == 1
    origin = store.get('song_origin:group:900000003')
    assert origin['requester'] == '33333' and origin['text'] == '大肥鱼 好久不见'


@pytest.mark.asyncio
async def test_real_turn_planner_prioritizes_name_call_over_later_chatter(turns, clock):
    e = turns
    e.cfg['name'] = '大肥鱼'
    e.plan['messages'] = ['好久不见，晚上好。']
    raw = message('大肥鱼 好久不见')
    raw['group_id'] = 99999
    await e.groups.receive(raw)
    raw = message('我们聊别的事', ident=2, sender=44444)
    raw['group_id'] = 99999
    await e.groups.receive(raw)
    await drain(e.groups)
    assert len(e.native) == 1 and e.native[0][0] == 'send_group_msg'
    prompt = e.calls[0][0]
    assert '"latest": "大肥鱼 好久不见"' in prompt and '"trigger": "name"' in prompt
    assert 'PRIVATE' not in prompt


@pytest.mark.asyncio
async def test_unsent_name_call_persists_across_restart_and_recovers_once(groups, clock):
    chats, store, cfg, calls, sent = groups
    cfg['name'] = '大肥鱼'

    async def fail(*args, **kwargs):
        raise APIConnectionError()

    chats.engine.generate = fail
    await chats.receive(message('大肥鱼 好久不见'))
    await drain(chats)
    job = store.get('reply_job:group:900000003')
    assert job['attempts'] == 1 and not sent
    restored = GroupChats(chats.plugin)
    restored.engine.config_reader = lambda: cfg
    clock[0] = job['due'] + 1
    restored.resume_pending()
    await drain(restored)
    assert len(sent) == 1 and not store.get('reply_job:group:900000003')
    restored.resume_pending()
    assert not restored.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['off', 'paused', 'outside', 'self', 'quota', 'unknown'])
async def test_name_calls_keep_existing_access_quota_and_uncertainty_rules(groups, clock, reason):
    chats, store, cfg, calls, sent = groups
    cfg['name'] = '大肥鱼'
    raw = message('大肥鱼你好')
    if reason == 'off':
        cfg['group_enabled'] = False
    elif reason == 'paused':
        cfg['paused'] = True
    elif reason == 'outside':
        raw['group_id'] = 99999
    elif reason == 'self':
        raw['user_id'] = 22222
    else:
        store.action('previous', 'group:900000003', 'reply', ['此前消息'])
        store.outcome('previous', 'succeeded' if reason == 'quota' else 'unknown')
        cfg['guest_daily_messages'] = 1
    await chats.receive(raw)
    await drain(chats)
    assert not calls and not sent
