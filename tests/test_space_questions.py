# ruff: noqa: E402
"""Questions in real public threads, with temporary state and fake transports."""

import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'app'), str(ROOT / 'astrbot')]
from own_space import OwnSpace, parse_answer
from qzone_vendor.core.model import Comment
from test_own_space import env as env


@pytest.fixture
def thread(env):
    cfg, store, raw, api, feature, calls, prompts = env
    store.set('known_friend_ids', ['11111'])
    raw.update(uin=11111, created_time=int(time.time()) - 30 * 86400)
    raw['commentlist'] = [dict(uin=67890, name='fish', tid=1, content='这幅配色很舒服。',
                              create_time=int(time.time()) - 90, list_3=[dict(
                                  uin=11111, name='friend', tid=2,
                                  content='@{uin:67890,nick:fish,who:1,auto:1}这幅是你画的吗？',
                                  create_time=int(time.time()) - 30)])]
    store.action('qzone_comment:11111:post1', 'qzone:11111', 'qzone_comment', ['这幅配色很舒服。'])
    store.outcome('qzone_comment:11111:post1', 'succeeded')
    return env


def decision(addressed=True, needs=True, content='我没有画过这幅，只是在说它的配色。'):
    return json.dumps(dict(addressed_to_me=addressed, needs_answer=needs,
                           reason='根据回复对象和提问内容判断', content=content), ensure_ascii=False)


def test_parser_retains_actual_recipient_and_original_content():
    content = '@{uin:67890,nick:fish,who:1,auto:1}想知道是哪一版'
    parsed = Comment.from_raw(dict(uin=11111, content=content, tid=2), parent_tid=1)
    assert parsed.reply_uin == 67890 and parsed.content == content and parsed.parent_tid == 1
    assert Comment.from_raw(dict(content='这不是实际@信息')).reply_uin is None


@pytest.mark.parametrize('raw', ['你好呀', '{}', decision(content=''),
                                decision(content='x' * 241),
                                decision().replace('true', '"true"'),
                                '<｜DSML｜invoke>'])
def test_invalid_decisions_cannot_become_public_comments(raw):
    with pytest.raises(ValueError):
        parse_answer(raw)


@pytest.mark.asyncio
async def test_one_format_correction_recovers_before_any_public_send(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    attempts = []

    async def generate(system, contexts, **kwargs):
        attempts.append(system)
        assert not calls
        return ('普通正文，缺少内部判断' if len(attempts) == 1 else decision()), None

    feature.plugin.engine.generate = generate
    assert (await feature.run())['replied'] == 1
    assert len(attempts) == 2 and len(calls) == 1
    assert '上一轮格式无效' in attempts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize('output', ['普通正文', '<｜DSML｜invoke>'])
async def test_repeated_bad_format_is_bounded_and_stays_pending_without_public_send(thread, output):
    cfg, store, raw, api, feature, calls, prompts = thread
    attempts = []

    async def generate(*args, **kwargs):
        attempts.append(True)
        return output, None

    feature.plugin.engine.generate = generate
    assert (await feature.run())['pending'] == 1
    await feature.run()
    assert len(attempts) == 2 and not calls


@pytest.mark.asyncio
async def test_question_on_participated_friend_post_answers_without_approval_and_only_once(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    store.incoming('private', '12345', 'PRIVATE_SECRET')
    store.remember('12345', 'PRIVATE_MEMORY')
    report = await feature.run()
    assert report['replied'] == 1 and calls[0][1:3] == (1, 11111)
    assert api.routing == [dict(comment_uin=67890, reply_uin=11111, reply_nick='friend')]
    assert 'PRIVATE' not in str(prompts)
    payload = json.loads(prompts[0][1][0]['content'])
    assert payload['self_id'] == '67890' and payload['post_author'] == '11111'
    assert payload['reply_to']['recipient'] == 67890
    assert not store.db.execute('SELECT 1 FROM qzone_authorizations').fetchone()
    assert store.action_count('qzone_comment', 0) == 1  # only the existing initial comment
    assert store.sent_count(0, kinds=('reply', 'proactive', 'friend_proactive')) == 0
    await OwnSpace(feature.plugin).run()
    assert len(calls) == len(prompts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('text,needs', [
    ('想知道你唱的是哪个版本', True), ('这幅是你画的吗？', True),
    ('哈哈，好看', False), ('好的，谢谢', False), ('你说的“这幅谁画的？”是在问别人吧', False),
])
async def test_semantic_decision_includes_question_without_mark_and_skips_closure(thread, text, needs):
    cfg, store, raw, api, feature, calls, prompts = thread
    raw['commentlist'][0]['list_3'][0]['content'] = '@{uin:67890,nick:fish}'+text

    async def generate(system, contexts, **kwargs):
        prompts.append((system, contexts))
        return decision(needs=needs, content='这是一个公开问题的回答。' if needs else ''), None

    feature.plugin.engine.generate = generate
    first = await feature.run()
    await feature.run()
    assert len(calls) == int(needs) and len(prompts) == 1
    assert first['replied' if needs else 'skipped'] >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['other_recipient', 'different_root', 'no_participation', 'old', 'removed_friend'])
async def test_other_people_questions_and_invalid_scope_never_call_model(thread, case):
    cfg, store, raw, api, feature, calls, prompts = thread
    target = raw['commentlist'][0]['list_3'][0]
    if case == 'other_recipient':
        target['content'] = '@{uin:22222,nick:other}你画的吗？'
    elif case == 'different_root':
        target['content'] = '这个多少钱？'
        raw['commentlist'].append(dict(uin=22222, name='other', tid=3, content='我的画',
                                      create_time=int(time.time()) - 60, list_3=[target]))
        raw['commentlist'][0]['list_3'] = []
    elif case == 'no_participation':
        raw['commentlist'][0]['uin'] = 22222
    elif case == 'old':
        target['create_time'] = int(time.time()) - 3 * 86400
    else:
        store.set('known_friend_ids', [])
    await feature.run()
    assert not calls and not prompts


@pytest.mark.asyncio
async def test_named_question_to_known_other_participant_is_not_a_bot_name_mistake(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    raw['commentlist'][0]['list_3'][0]['content'] = '小明，这幅是你画的吗？'
    raw['commentlist'].append(dict(uin=22222, name='小明', tid=8, content='我来了',
                                    create_time=int(time.time())-40, list_3=[]))
    await feature.run()
    assert not calls and not prompts


@pytest.mark.asyncio
@pytest.mark.parametrize('text,eligible', [('小明，这幅是你画的吗？', False),
                                        ('请问，这幅是你画的吗？', True),
                                        ('我想知道，这是哪一版', True),
                                        ('fish，这幅是你画的吗？', True)])
async def test_bare_other_name_is_not_reinterpreted_as_bot_while_question_openings_work(thread, text, eligible):
    cfg, store, raw, api, feature, calls, prompts = thread
    raw['commentlist'][0]['list_3'][0]['content'] = text
    await feature.run()
    assert len(calls) == int(eligible) and len(prompts) == int(eligible)


@pytest.mark.asyncio
async def test_explicit_question_in_new_root_of_watched_post_can_address_bot(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    target = raw['commentlist'][0]['list_3'].pop()
    target.update(tid=5, list_3=[])
    raw['commentlist'].append(target)
    await feature.run()
    assert len(calls) == 1 and calls[0][1:3] == (5, 11111)


@pytest.mark.asyncio
async def test_manual_answer_suppresses_old_question_but_new_followup_can_continue(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    root = raw['commentlist'][0]
    root['list_3'].append(dict(uin=67890, name='fish', tid=3,
                               content='@{uin:11111,nick:friend}已经回答过的问题。',
                               create_time=int(time.time()) - 15))
    await feature.run()
    assert not calls and not prompts
    root['list_3'].append(dict(uin=11111, name='friend', tid=4,
                               content='@{uin:67890,nick:fish}那这幅呢？', create_time=int(time.time()) - 5))
    await feature.run()
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['edit', 'delete', 'retarget', 'post_edit', 'new_answer', 'pause_after_read'])
async def test_changes_during_generation_or_final_read_prevent_stale_public_reply(thread, change):
    cfg, store, raw, api, feature, calls, prompts = thread
    original = feature.plugin.engine.generate

    async def generate(*args, **kwargs):
        result = await original(*args, **kwargs)
        target = raw['commentlist'][0]['list_3'][0]
        if change == 'edit':
            target['content'] += '，不是问你'
        elif change == 'delete':
            raw['commentlist'][0]['list_3'] = []
        elif change == 'retarget':
            target['content'] = '@{uin:22222,nick:other}你画的吗？'
        elif change == 'post_edit':
            raw['content'] = '改过的动态正文'
        elif change == 'new_answer':
            raw['commentlist'][0]['list_3'].append(dict(uin=67890, name='fish', tid=3,
                content='@{uin:11111,nick:friend}已经回答', create_time=int(time.time())))
        return result

    feature.plugin.engine.generate = generate
    if change == 'pause_after_read':
        original_read = api.get_detail
        reads = []

        async def detail(post):
            reads.append(True)
            result = await original_read(post)
            if len(reads) == 2:
                cfg['paused'] = True
            return result

        api.get_detail = detail
    await feature.run()
    assert not calls


@pytest.mark.asyncio
async def test_changed_nonquestion_can_be_reconsidered_as_new_question(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    original = feature.plugin.engine.generate

    async def skip(*args, **kwargs):
        return decision(needs=False, content=''), None

    feature.plugin.engine.generate = skip
    await feature.run()
    raw['commentlist'][0]['list_3'][0]['content'] += '想知道是哪一版'
    feature.plugin.engine.generate = original
    await feature.run()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_unknown_friend_reply_readback_with_qq_prefix_is_reconciled_without_resend(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    original = api.reply

    async def uncertain(*args, **kwargs):
        await original(*args, **kwargs)
        raise TimeoutError()

    api.reply = uncertain
    await feature.run()
    assert store.db.execute("SELECT state FROM actions WHERE kind='qzone_incoming_reply'").fetchone()[0] == 'unknown'
    await feature.run()
    assert len(calls) == len(prompts) == 1
    assert store.db.execute("SELECT state FROM actions WHERE kind='qzone_incoming_reply'").fetchone()[0] == 'succeeded'


@pytest.mark.asyncio
async def test_thread_selection_does_not_mix_reused_reply_ids_from_other_roots(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    raw['commentlist'].append(dict(uin=22222, name='other', tid=9, content='OTHER_ROOT',
        create_time=int(time.time())-60, list_3=[dict(uin=33333, name='third', tid=1,
            content='OTHER_ROOT_REPLY', create_time=int(time.time())-20)]))
    await feature.run()
    assert len(calls) == 1 and 'OTHER_ROOT_REPLY' not in str(prompts)


def test_watches_expire_and_are_bound_to_account_owner(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    feature._watch(cfg, '11111', 'recent', time.time())
    feature._watch(cfg, '11111', 'old', time.time() - 8 * 86400)
    assert [p.tid for p in feature.watched_posts(cfg, read_only=True)] == ['recent']
    changed = dict(cfg, owner_qq='33333')
    assert not feature.watched_posts(changed, read_only=True)
    store.db.execute("DELETE FROM actions")
    assert [p.tid for p in feature.watched_posts(cfg)] == ['recent']
    assert not store.get('space_watch:67890:11111:old')


@pytest.mark.asyncio
async def test_probe_does_not_publish_or_register_new_watches(thread):
    cfg, store, raw, api, feature, calls, prompts = thread
    await feature.run(read_only=True)
    assert not calls and not prompts
    assert not store.db.execute("SELECT 1 FROM state WHERE key LIKE 'space_watch:%'").fetchone()


@pytest.mark.asyncio
async def test_question_poll_is_independent_of_slow_autonomous_scheduler(env, monkeypatch):
    cfg, store, raw, api, feature, calls, prompts = env
    monkeypatch.setenv('QQCOMPANION_ROOT', str(ROOT))
    spec = importlib.util.spec_from_file_location('space_poll_test', ROOT/'plugins/astrbot_plugin_qq_companion/main.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'read_settings', lambda: cfg)
    ran = []

    async def check():
        ran.append('space')

    fake = SimpleNamespace(bot=object(), store=store, reply_queue=SimpleNamespace(ready=lambda: []),
                           own_space=feature, own_space_task=None, check_own_space=check)
    real_sleep = asyncio.sleep

    async def stop(_):
        await real_sleep(0)
        raise asyncio.CancelledError()

    monkeypatch.setattr(module.asyncio, 'sleep', stop)
    with pytest.raises(asyncio.CancelledError):
        await module.QQCompanion.recover_replies(fake)
    await fake.own_space_task
    assert ran == ['space']


@pytest.mark.asyncio
async def test_online_check_ignores_hour_timer_once_and_reconnect_checks_again(env):
    cfg, store, raw, api, feature, calls, prompts = env
    store.set('own_space_scan_at:67890', time.time())
    assert not feature.due(cfg)
    feature.on_online(cfg)
    assert feature.due(cfg)
    assert (await feature.run(scheduled=True))['online_check']
    assert not feature.due(cfg) and (await feature.run(scheduled=True))['waiting']
    feature.on_online(cfg)  # A later real offline -> online transition.
    assert (await feature.run(scheduled=True))['online_check']
    assert not feature.due(cfg) and len(calls) == 1  # Checking is not resending.


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['connection', 'feed'])
async def test_failed_online_read_remains_pending_and_respects_retry_delay(env, failure):
    cfg, store, raw, api, feature, calls, prompts = env
    store.set('own_space_scan_at:67890', time.time())
    feature.on_online(cfg)
    original_connect, original_feed = feature.plugin.social.ensure_qzone, api.get_feeds

    async def unavailable(*args, **kwargs):
        raise RuntimeError('connection unavailable')

    async def rejected(*args, **kwargs):
        return SimpleNamespace(ok=False)

    if failure == 'connection':
        feature.plugin.social.ensure_qzone = unavailable
    else:
        api.get_feeds = rejected
    with pytest.raises(RuntimeError):
        await feature.run(scheduled=True)
    assert feature.online_epoch != feature.checked_online_epoch
    store.set('space_retry_after:67890', time.time() + 60)
    assert not feature.due(cfg)
    store.set('space_retry_after:67890', time.time() - 1)
    feature.plugin.social.ensure_qzone, api.get_feeds = original_connect, original_feed
    assert (await feature.run(scheduled=True))['online_check']
    assert not feature.due(cfg)


@pytest.mark.asyncio
async def test_new_online_transition_during_read_is_not_consumed_by_older_check(env):
    cfg, store, raw, api, feature, calls, prompts = env
    feature.on_online(cfg)
    original = api.get_feeds
    reads = []

    async def get_feeds(*args, **kwargs):
        reads.append(True)
        if len(reads) == 1:
            feature.on_online(cfg)
        return await original(*args, **kwargs)

    api.get_feeds = get_feeds
    await feature.run(scheduled=True)
    assert feature.due(cfg)
    await feature.run(scheduled=True)
    assert not feature.due(cfg) and len(reads) == 2 and len(calls) == 1


@pytest.mark.asyncio
async def test_readonly_probe_does_not_consume_the_online_check(env):
    cfg, store, raw, api, feature, calls, prompts = env
    feature.on_online(cfg)
    await feature.run(read_only=True)
    assert feature.due(cfg) and not calls and not prompts
    await feature.run(scheduled=True)
    assert not feature.due(cfg)


@pytest.mark.asyncio
@pytest.mark.parametrize('gate', ['live', 'paused', 'qzone', 'qzone_reply_own'])
async def test_online_check_keeps_existing_permissions(env, gate):
    cfg, store, raw, api, feature, calls, prompts = env
    feature.on_online(cfg)
    cfg[gate] = gate == 'paused'
    assert not feature.due(cfg)
    await feature.run(scheduled=True)
    assert not calls and not prompts


@pytest.mark.asyncio
@pytest.mark.parametrize('context', ['direct_reply_to_self', 'own_post_author', 'infer_from_thread'])
async def test_second_person_hint_is_bound_to_actual_reply_relationship(thread, context):
    cfg, store, raw, api, feature, calls, prompts = thread
    if context == 'own_post_author':
        raw['uin'] = 67890
        raw['commentlist'] = [dict(uin=11111, name='friend', tid=5,
                                  content='你唱的是哪个版本', create_time=int(time.time())-10, list_3=[])]
        store.db.execute('DELETE FROM actions')
    elif context == 'infer_from_thread':
        raw['commentlist'][0]['list_3'][0]['content'] = '你唱的是哪个版本'
    else:
        raw['commentlist'][0]['list_3'][0]['content'] = '@{uin:67890,nick:fish}你唱的是哪个版本'
    await feature.run()
    assert len(calls) == 1
    system, messages = prompts[0]
    payload = json.loads(messages[0]['content'])
    assert payload['second_person_context'] == context
    assert payload['self_id'] != payload['reply_to']['author']
    assert '“你”指你本人' in system and '不能自行认定对方叫错了你的名字' in system
