"""Reply decoding preserves media choices and the established public protocol."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from engine import EmptyReplyError, Engine  # noqa: E402
from reply_parser import parse_chat_reply, parse_decision  # noqa: E402
from stickers import StickerLibrary  # noqa: E402


@pytest.fixture
def cfg():
    return dict(owner_qq="12345", revision=77, send_memes=True)


@pytest.fixture
def stickers():
    library = StickerLibrary.__new__(StickerLibrary)
    library.items = {"happy": {}, "085_master": {}}
    return library


def test_chat_media_survive_decoding_and_internal_markers_stay_out_of_text(
    cfg, stickers
):
    result = parse_chat_reply(
        "好呀\n[语音]\n[心情:开心][画画:月亮下的小鲸鱼][QQ表情:比心]\n[表情:happy]",
        cfg,
        12,
        contact="12345",
        stickers=stickers,
    )
    assert result["messages"] == ["好呀"]
    assert result["voice_selected"] is True
    assert result["mood"] == "开心"
    assert result["drawing_brief"] == "月亮下的小鲸鱼"
    assert result["sticker_id"] == "happy"
    assert result["revision"] == 77 and result["read_through"] == 12


@pytest.mark.parametrize(
    "contact,enabled,expected",
    [
        ("12345", True, "085_master"),
        ("99999", True, None),
        ("12345", False, None),
    ],
)
def test_owner_sticker_and_feature_switch_remain_enforced(
    cfg, stickers, contact, enabled, expected
):
    cfg["send_memes"] = enabled
    result = parse_chat_reply(
        "[表情:085_master]", cfg, 0, contact=contact, stickers=stickers
    )
    assert result["sticker_id"] == expected
    assert result["messages"] == ([""] if expected else ["🐟"])


def test_recent_sticker_is_read_once_only_when_text_and_sticker_need_comparison(
    cfg, stickers
):
    reads = []

    def recent():
        reads.append(True)
        return dict(id="happy", at=time.time())

    result = parse_chat_reply(
        "好的[表情:happy]",
        cfg,
        0,
        contact="12345",
        stickers=stickers,
        recent_sticker=recent,
    )
    assert reads == [True]
    assert result["sticker_id"] is None and result["messages"] == ["好的"]


@pytest.mark.parametrize(
    "text,contact",
    [
        ("普通文字", "12345"),
        ("[表情:happy]", "12345"),
        ("你好[表情:085_master]", "99999"),
    ],
)
def test_normal_text_pure_stickers_and_rejected_stickers_do_not_query_recent_delivery(
    cfg, stickers, text, contact
):
    def unexpected():
        pytest.fail("No recent delivery lookup is needed for this response")

    parse_chat_reply(
        text, cfg, 0, contact=contact, stickers=stickers, recent_sticker=unexpected
    )


def test_same_turn_voice_selection_survives_without_a_text_marker(cfg):
    result = parse_chat_reply(
        "你回来啦。", cfg, 0, contact="12345", turn_plan=dict(voice_selected=True)
    )
    assert result["voice_selected"] is True
    assert result["messages"] == ["你回来啦。"]


def test_discussing_a_voice_marker_inside_a_sentence_keeps_it_as_literal_text(cfg):
    text = "这段文字讨论[语音]这个标记"
    result = parse_chat_reply(text, cfg, 0, contact="12345")
    assert result["messages"] == [text]
    assert result["voice_selected"] is False


def test_public_sharing_fields_and_legacy_entry_point_are_preserved(cfg):
    text = json.dumps(
        dict(
            action="reply",
            messages=["  公开小事  "],
            share_owner_chat=True,
            share_drawing=True,
            drawing_brief="  月亮下的小鲸鱼  ",
            drawing_id="art-1",
        )
    )
    result = parse_decision("```json\n" + text + "\n```", cfg, 12)
    assert result == Engine.parse_decision(text, cfg, 12)
    assert result["share_owner_chat"] and result["share_drawing"]
    assert result["drawing_brief"] == "月亮下的小鲸鱼"
    assert result["drawing_id"] == "art-1"
    assert result["messages"] == ["公开小事"]


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "reply", "messages": [""]},
        {"action": "reply", "messages": ["   ", "\n"]},
    ],
)
def test_empty_structured_reply_uses_the_engine_retry_exception(cfg, payload):
    with pytest.raises(EmptyReplyError):
        parse_decision(json.dumps(payload), cfg, 0)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"action": "execute", "messages": ["不能执行"]},
        {"action": "reply", "messages": "不是列表"},
        {"action": "reply", "messages": [42]},
        {"action": "reply", "messages": ["一", "二", "三"]},
        {"action": "reply", "messages": ["鱼" * 1201]},
    ],
)
def test_invalid_structured_messages_are_rejected(cfg, payload):
    with pytest.raises(ValueError):
        parse_decision(json.dumps(payload), cfg, 0)
