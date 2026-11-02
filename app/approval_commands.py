"""Route explicit approval commands before any model interpretation.

Only whole commands are accepted. Quoted instructions, questions and natural
conversation still belong to the conversation planner, not this parser.
"""

import re
from dataclasses import dataclass


_NAMES = {
    '同意动态': ('post', True), '拒绝动态': ('post', False),
    '同意评论': ('comment', True), '拒绝评论': ('comment', False),
    '待发动态': ('post', None), '动态草稿': ('post', None),
    '待审批': ('comment', None),
}
_START = re.compile(r'[/／]?\s*(' + '|'.join(_NAMES) + r')([\s\S]*)')


@dataclass(frozen=True)
class ApprovalCommand:
    domain: str
    approve: bool | None
    token: str
    canonical: str
    error: str = ''


def parse_approval_command(text):
    text = text.strip()
    match = _START.fullmatch(text)
    if not match:
        return None
    name, tail = match.groups()
    domain, approve = _NAMES[name]
    if approve is None:
        if not re.fullmatch(r'\s*[。！!．.]?\s*', tail):
            return None  # E.g. “待发动态是什么功能” is a question.
        return ApprovalCommand(domain, approve, '', '/' + name)
    size = '8' if domain == 'post' else '6,8'
    token = re.fullmatch(r'\s*([0-9a-fA-F]{' + size + r'})\s*[。！!．.]?\s*', tail)
    if token:
        value = token[1].lower()
        return ApprovalCommand(domain, approve, value, '/' + name + ' ' + value)
    # A sentence about commands is not itself an approval. An explicit slash,
    # empty argument or argument boundary does identify a malformed command.
    if tail and not tail[0].isspace() and text[0] not in '/／':
        return None
    return ApprovalCommand(domain, approve, '', '/' + name,
                           f'没有执行。请用 {name} 八位编号（前面的 / 可以省略）；不要把其他话接在审批命令后。')


async def dispatch_approval(plugin, contact, text, *, source, source_time):
    """Shared QQ/desktop entry; domain executors remain the source of truth."""
    command = parse_approval_command(text)
    if command is None:
        return None
    cfg = plugin.engine.config_reader()
    if contact != cfg['owner_qq']:
        return '动态和评论审批只在主人私聊中处理。'
    if command.error:
        return command.error
    if command.domain == 'post':
        return plugin.autopost.draft_command(contact, command.canonical, source_time=source_time)
    return await plugin.social.approval_command(contact, command.canonical,
                                                source=source, source_time=source_time)
