"""Decode model output; execution and delivery remain the engine's responsibility."""

import json
import time
from collections.abc import Callable

from local_voice import VOICE_MARK
from personality import split_reply
from qq_faces import MARKER as FACE_MARKER
from sketchbook import DRAWING_MARK, MOOD_MARK


class EmptyReplyError(ValueError):
    """A generation completed without any visible reply text."""


def parse_decision(response, cfg, read_through):
    response = response.strip()
    if response.startswith("```json\n") and response.endswith("```"):
        response = response[8:-3].strip()
    obj = json.loads(response)
    if not isinstance(obj, dict) or obj.get("action") not in {"reply", "skip"}:
        raise ValueError("Invalid decision")
    lines = obj.get("messages", [])
    if not isinstance(lines, list) or any(not isinstance(x, str) for x in lines):
        raise ValueError("Invalid message list")
    lines = [s.strip() for s in lines if s.strip()]
    if len(lines) > 2 or sum(map(len, lines)) > 1200:
        raise ValueError("Message size exceeded")
    if obj["action"] == "reply" and not lines:
        raise EmptyReplyError("Empty reply")
    return dict(
        action=obj["action"],
        messages=lines,
        memory=obj.get("memory", []),
        share_drawing=obj.get("share_drawing") is True,
        share_owner_chat=obj.get("share_owner_chat") is True,
        drawing_brief=obj["drawing_brief"].strip()
        if isinstance(obj.get("drawing_brief"), str)
        and 0 < len(obj["drawing_brief"].strip()) <= 200
        else None,
        drawing_id=obj.get("drawing_id")
        if isinstance(obj.get("drawing_id"), str) and len(obj["drawing_id"]) <= 128
        else None,
        revision=cfg["revision"],
        read_through=read_through,
    )


def parse_chat_reply(
    response,
    cfg,
    read_through,
    *,
    contact,
    stickers=None,
    turn_plan=None,
    recent_sticker: Callable[[], dict] | None = None,
):
    """Decode ordinary text and compatible envelopes, retaining media selections."""
    # Decode a legacy envelope if a model emits one, but normal
    # conversational text no longer depends on JSON parsing.
    try:
        legacy = json.loads(response)
    except ValueError:
        legacy = None
    if isinstance(legacy, dict) and "action" in legacy and "messages" in legacy:
        decoded = parse_decision(response, cfg, read_through)
        if decoded["action"] == "skip":
            return decoded
        response = "\n\n".join(decoded["messages"])
    voice_selected = bool(VOICE_MARK.search(response)) or bool(
        turn_plan and turn_plan.get("voice_selected")
    )
    response = VOICE_MARK.sub("", response).strip()
    moods = MOOD_MARK.findall(response)
    mood = moods[-1] if moods else None
    response = MOOD_MARK.sub("", response).strip()
    drawings = DRAWING_MARK.findall(response)
    drawing_brief = drawings[-1] if drawings else None
    response = DRAWING_MARK.sub("", response).strip()
    sticker_id = None
    if stickers:
        response, sticker_id = stickers.extract(response)
        if sticker_id and (
            not cfg.get("send_memes", False)
            or (contact != cfg["owner_qq"] and sticker_id == "085_master")
        ):
            sticker_id = None
        if sticker_id and FACE_MARKER.sub("", response).strip():
            last_sticker = recent_sticker() if recent_sticker else {}
            if (
                last_sticker.get("id") == sticker_id
                and time.time() - last_sticker.get("at", 0) < 45
            ):
                sticker_id = None
    if sticker_id:
        response = FACE_MARKER.sub("", response).strip()
    if not response and not sticker_id and not drawing_brief:
        response = "🐟"
    return dict(
        action="reply",
        messages=split_reply(response) if response else [""],
        memory=[],
        revision=cfg["revision"],
        read_through=read_through,
        sticker_id=sticker_id,
        drawing_brief=drawing_brief,
        mood=mood,
        voice_selected=voice_selected,
    )
