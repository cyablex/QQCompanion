"""Canonical ordinary speech, separate from program-owned task receipts.

These checks classify model output, never user requests or task authorization.
"""

import re


# These are model protocol tokens, never ordinary chat or executable tasks.
_INTERNAL_CALL = re.compile(
    r'<\s*/?\s*(?:[|｜]{1,2}DSML[|｜]{1,2}|tool_calls?\b|function(?:_calls?)?\b|invoke\b)', re.I)


class InternalCallError(ValueError):
    """A model printed a tool-call protocol instead of a supported reply."""


def ensure_user_text(text):
    if _INTERNAL_CALL.search(text):
        raise InternalCallError('回复含内部调用标记；请用规定的JSON任务或自然正文重新输出。')


_QUOTED = re.compile(r'“[^”]*”|‘[^’]*’|"[^"\n]*"|\'[^\'\n]*\'')
_CLAUSE_END = re.compile(r'[。！？!?；;，,\n]+[ \t]*|(?<!\d)\.(?!\d)[ \t]*')
ACTION_CLAIM = re.compile(
    r"(?:那我|我来|我给你|这就|马上).{0,8}(?:唱|画|发歌|发图|发评论|改签名)|"
    r"(?:已经|正在|在弄了|已排队|稍等).{0,14}(?:歌曲|唱|画|签名|评论|发给你)|"
    r"(?:歌|图|签名|评论).{0,8}(?:发好了|发出去了|改好了|画好了|正在准备)|"
    r"我(?:已经)?(?:发|唱|画|改)(?:好|完)(?:了|啦)|"
    r"(?:这就|马上|现在就|正在)(?:给你|帮你|替你|为你)?(?:安排|处理|制作|生成|发送|发布|画|唱)|"
    r"(?:我)?(?:已经)?(?:弄|做|办)(?:好|完)(?:了|啦)|"
    r"^(?:我)?(?:已(?:经)?|刚(?:刚)?)(?:唱完|唱好|画完|画好|发出|发送|发好|发完|改好|改成|修改好|修改为|设置为|保存|记录|做好|弄好|办好|完成|生成|发布)|"
    r"\bI(?:'ll| will)\s+(?:sing|draw|send|post|change|update)\b|"
    r"\bI (?:have |already )?(?:sent|posted|finished|completed)\b",
    re.I,
)
_TASK_STATUS = re.compile(
    r"(?:那首|这首|那张|这张|这幅|那幅|歌曲|歌声|画作|图片|签名|评论|任务|音源)[^。！？!?；;，,\n]{0,14}"
    r"(?:还没|尚未|没(?:有)?(?:好|弄好|做完|完成|发出)|未完成|已完成|已经|正在|准备中|制作中|生成中|排队|等待|失败|成功)|"
    r"(?:正在|已经|尚未|还没|等待)[^。！？!?；;，,\n]{0,8}(?:制作|生成|绘制|合成|发送|发布|修改|处理)|"
    r"(?:还没|尚未)(?:弄好|做好|画好|唱完|做完|弄完|准备好|完成)|"
    r"^(?:尚未|还没|未)(?:唱完|画完|发出|发送|做完|做好|生成|合成|发布)|"
    r"(?:song|drawing|image|task).{0,18}(?:not ready|not finished|pending|processing|completed|already sent)",
    re.I,
)
_TASK_ASIDE = re.compile(
    r"^(?:好(?:的|呀|啊|哒)?|行(?:呀|啊)?|嗯(?:嗯)?|收到|没问题|可以(?:的|呀)?|"
    r"给你安排(?:啦|呀|吧|哦)?|交给我(?:吧|啦|呀|哦)?|稍等(?:我)?(?:一会儿?|一下)?|等(?:我)?(?:一会儿?|一下)|给你看看|"
    r"OK|okay|sure)[。！？!?；;，,\s]*$",
    re.I,
)


def task_narration(text):
    """Whether a model clause speaks for execution that needs a real receipt."""
    text = text.strip()
    # Quoted words inside an explanation are evidence being discussed, not the
    # assistant asserting the quoted operation. A bare quoted reply is still
    # checked, so wrapping an assertion in quotation marks cannot bypass it.
    text = re.sub(r'https?://[^\s<>()]+', '', text)
    outside = _QUOTED.sub('', text)
    checked = outside if re.search(r'[A-Za-z0-9\u3400-\u9fff]', outside) else text
    return bool(ACTION_CLAIM.search(checked) or _TASK_STATUS.search(checked))


def _song_invitation(text):
    checked = _QUOTED.sub('', text)
    return bool(re.search(r'(?:要不要|想不想|我们|一起|先).{0,24}(?:听|唱)|给你(?:听|唱)|听一耳朵', checked))


def _legacy_chat(text, *, inviting=False):
    # A bubble may contain both a task promise and a genuine answer. Keep the
    # latter instead of dropping the complete bubble when one clause is unsafe.
    quotes = [match.span() for match in _QUOTED.finditer(text)]
    parts, start = [], 0
    for ending in _CLAUSE_END.finditer(text):
        if any(left <= ending.start() < right for left, right in quotes):
            continue
        parts.append((text[start:ending.start()], ending.group()))
        start = ending.end()
    parts.append((text[start:], ''))
    retained = []
    for clause, ending in parts:
        clause = clause.strip()
        if clause and not task_narration(clause) and not _TASK_ASIDE.fullmatch(clause) and not (inviting and _song_invitation(clause)):
            retained.append(clause + ending)
    return ''.join(retained).strip('，,；;\n ')


def _texts(value):
    if not isinstance(value, list) or any(not isinstance(text, str) for text in value):
        raise ValueError('Reply content must be a list of text bubbles')
    for text in value:
        ensure_user_text(text)
    return value


def independent_chat(turn):
    """Merge both supported output fields into at most two ordinary bubbles.

    Legacy task narration is left to the executor. Explicit independent speech
    must already be ordinary prose; invalid claims trigger format recovery.
    """
    tasks = turn.get('turn_actions') or turn.get('tasks', [])
    legacy = _texts(turn.get('messages', []))
    explicit = _texts(turn.get('chat_messages', []))
    inviting = any(t.get('type') == 'song.offer' for t in tasks)
    if tasks and any(task_narration(text) or (inviting and _song_invitation(text)) for text in explicit):
        raise ValueError('Independent chat cannot assert task progress or completion')
    candidates = ([_legacy_chat(text, inviting=inviting) for text in legacy] if tasks else legacy) + explicit
    result, seen = [], set()
    for text in candidates:
        text = text.strip()
        identity = re.sub(r'\s+', ' ', text)
        if text and identity not in seen:
            result.append(text)
            seen.add(identity)
    if len(result) > 2:
        result = [result[0], '\n\n'.join(result[1:])]
    if sum(map(len, result)) > 1200:
        raise ValueError('Combined ordinary reply exceeds 1200 characters')
    return result


def append_references(messages, sources):
    """Attach a verified source to the speech that will actually be sent."""
    text = '\n\n'.join(messages)
    ordered = sorted(sources, key=lambda row: row.get('kind') == 'page_excerpt', reverse=True)
    links = [row['url'] for row in ordered if len(row.get('url', '')) <= 350][:2]
    if not links or any(url in text for url in links):
        return list(messages)
    refs = '\n\n参考：\n' + links[0]
    available = 1200 - len(refs)
    if len(text) > available:
        text = text[:available - 1].rstrip() + '…'
    return [(text + refs).lstrip()]
