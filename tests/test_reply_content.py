"""Both supported reply fields reach the actual ordinary-speech channel."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from reply_content import append_references, independent_chat, task_narration  # noqa: E402
from test_turn_actions import turns as turns, task  # noqa: E402
from test_singing import songs as songs, ready, incoming  # noqa: E402,F401


def mixed(messages, chat=None):
    return dict(messages=messages, chat_messages=chat or [], turn_actions=[dict(type='song.list')])


def test_legacy_answer_survives_alongside_task_and_duplicate_explicit_field():
    answer = '动态主要写我自己感兴趣的东西。'
    assert independent_chat(mixed([answer])) == [answer]
    assert independent_chat(mixed([answer], [answer])) == [answer]


def test_mixed_bubble_keeps_answer_but_leaves_execution_claims_to_receipts():
    turn = mixed(['好呀，我给你唱。动态主要写我自己感兴趣的东西，配图喜欢柔和一点。'])
    assert independent_chat(turn) == ['动态主要写我自己感兴趣的东西，配图喜欢柔和一点。']
    assert independent_chat(mixed(['歌已经发好了。', '稍等我一下。'])) == []


@pytest.mark.parametrize('text', ['那首还没好。', '图片正在生成。', '我已经发好了。', '正在帮你处理。'])
def test_explicit_independent_chat_rejects_task_status_and_promises(text):
    assert task_narration(text)
    with pytest.raises(ValueError, match='task progress'):
        independent_chat(mixed([], [text]))


@pytest.mark.parametrize('text', ['已唱完', '已画好', '已发出', '已经发布', '尚未发送', '还没做好',
                                  '  已唱完  ', '已经发好了', '已改成慢慢来'])
def test_short_execution_results_remain_program_owned_receipts(text):
    assert task_narration(text)
    assert independent_chat(mixed([text])) == []


def test_discussion_of_quoted_words_is_not_an_execution_claim():
    answer = '台词里“我给你唱”的意思是主动提出唱歌。'
    assert independent_chat(mixed([answer])) == [answer]
    assert not task_narration(answer)
    assert task_narration('“我已经发好了。”')
    quoted = '台词里“我给你唱，等一会儿”的意思是主动提出唱歌。'
    assert independent_chat(mixed([quoted])) == [quoted]


def test_english_mixed_answer_keeps_prose_after_the_task_promise():
    assert independent_chat(mixed(["I'll sing it. My posts reflect my own interests."])) == [
        'My posts reflect my own interests.']


def test_no_task_both_fields_merge_without_dropping_ordinary_content():
    result = independent_chat(dict(messages=['我喜欢蓝色。', '配色可以浅一点。'],
                                   chat_messages=['也喜欢有留白的画。', '我喜欢蓝色。']))
    assert len(result) <= 2
    assert all(text in '\n'.join(result) for text in ['我喜欢蓝色。', '配色可以浅一点。', '也喜欢有留白的画。'])


def test_combined_field_overflow_is_rejected_instead_of_silently_truncated():
    with pytest.raises(ValueError, match='1200'):
        independent_chat(mixed(['甲' * 700], ['乙' * 700]))


def test_reference_already_present_is_not_repeated_and_long_body_keeps_link():
    url = 'https://example.org/verified-source'
    source = dict(url=url, title='test')
    assert append_references(['有依据的回答。\n' + url], [source]) == ['有依据的回答。\n' + url]
    result = append_references(['甲' * 1200], [source])
    assert url in result[0] and sum(map(len, result)) <= 1200
    documented_status = 'https://example.org/task/completed'
    sourced = mixed([], ['状态文档在这里：' + documented_status])
    assert independent_chat(sourced) == sourced['chat_messages']


@pytest.mark.asyncio
async def test_real_executor_delivers_legacy_ordinary_answer_with_song(turns):
    e = turns
    ready(e)
    incoming(e, '唱司凤版雨爱，顺便说说你的动态风格。')
    task(e, 'song.request', title='雨爱', artist='司凤')
    e.plan['messages'] = ['动态主要写我自己感兴趣的东西。']
    assert await e.engine.reply('12345')
    assert any(m[2].get('song_file') for m in e.sent)
    assert sum('动态主要' in m[1] for m in e.sent) == 1


@pytest.mark.asyncio
async def test_web_references_follow_mixed_task_ordinary_answer(turns, monkeypatch):
    e = turns
    ready(e)
    e.cfg['web_search'] = True
    incoming(e, '列一下曲库，顺便查一下测试城市的温度。')
    plan = dict(action='reply', messages=[], tasks=[dict(type='song.list')], chat_messages=['测试城市现在20度。'])
    e.responses = ['[搜索:测试城市温度]', json.dumps(plan, ensure_ascii=False)]
    url = 'https://example.org/verified-mixed-source'

    async def lookup(*args, **kwargs):
        return dict(query='测试城市温度', sources=[dict(title='隔离测试来源', url=url, snippet='20度')])

    monkeypatch.setattr(e.engine.web, 'lookup', lookup)
    assert await e.engine.reply('12345')
    answer = next(m[1] for m in e.sent if '20度' in m[1])
    assert url in answer
