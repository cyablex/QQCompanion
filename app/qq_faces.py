"""Names from installed QQ/NapCat, and native OneBot face output."""

import json
import re
from pathlib import Path

FACE_NAMES = {
    int(k): v
    for k, v in json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"))[
        "faces"
    ].items()
}
FACE_IDS = {name: str(number) for number, name in FACE_NAMES.items()}
OUTPUT_NAMES = [
    n
    for n in (
        "呲牙",
        "偷笑",
        "憨笑",
        "流泪",
        "疑问",
        "可怜",
        "笑哭",
        "捂脸",
        "比心",
        "爱心",
        "抱拳",
        "点赞",
        "吃瓜",
    )
    if n in FACE_IDS
]
MARKER = re.compile(r"\[QQ表情[:：]([^\]\n]{1,24})\]")


def prompt():
    return (
        "也能使用一个 QQ 原生表情，语法是 [QQ表情:名字]，可选："
        + "、".join(OUTPUT_NAMES)
        + "。轻松闲聊中主动用表情传达语气，开心、疑惑、吐槽、惊讶、答应都适用；简短回应也可以只发一个表情。"
        "有鲸鱼娘表情可选时，两类选一种，自己的贴图更贴切就用贴图；每轮最多一个，不必每轮都有；不连续刷相同表情，严肃讨论或倾诉先照顾内容。"
        "不解释这个标记，不把收到的表情逐个念出来。"
    )


def segments(text, allow_face=True):
    """Keep face position; send at most one validated native face per bubble."""
    result, offset, used = [], 0, False
    for match in MARKER.finditer(text):
        if match.start() > offset:
            result.append(
                dict(type="text", data={"text": text[offset : match.start()]})
            )
        name = match.group(1).strip()
        if allow_face and not used and name in OUTPUT_NAMES:
            result.append(dict(type="face", data={"id": FACE_IDS[name]}))
            used = True
        offset = match.end()
    if offset < len(text):
        result.append(dict(type="text", data={"text": text[offset:]}))
    if not result:
        result = [dict(type="text", data={"text": "嗯"})]
    return result
