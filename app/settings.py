"""Local configuration and Windows user-bound credential storage."""

import ctypes
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from ctypes import wintypes
from pathlib import Path

ROOT = Path(os.environ.get("QQCOMPANION_ROOT", Path(__file__).resolve().parents[1]))

# This is the total generation budget, including thinking; visible chat remains
# bounded separately by the conversation and delivery policies.
MODEL_MAX_OUTPUT_TOKENS = 8192
MODEL_REQUEST_TIMEOUT = 120
MODEL_TASK_TIMEOUT = 150


def quota_unlimited(cfg, now=None):
    moment = datetime.now(timezone(timedelta(hours=8))) if now is None else now
    return cfg.get("quota_unlimited_day") == moment.strftime("%Y-%m-%d")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + secrets.token_hex(6) + ".new")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def read_settings():
    path = ROOT / "config" / "user.json"
    defaults = {
        "owner_qq": "",
        "bot_qq": "",
        "name": "小伴",
        "persona": "自然、温和，偶尔轻松打趣。回复简短但认真，不刻意撒娇。",
        "owner_title": "主人",
        "model": "deepseek-flash",
        "model_route": "official",
        "live": False,
        "paused": False,
        "guests": False,
        "proactive": False,
        "friend_proactive": False,
        "friend_proactive_daily_max": 3,
        "friend_proactive_interval_hours": 1.5,
        "proactive_global_gap_minutes": 5,
        "web_search": False,
        "web_daily_requests": 60,
        "memory_enabled": True,
        "conversation_followups": True,
        "digital_activities": True,
        "autonomous_drawing": False,
        "autonomous_profile": False,
        "chat_style_review": False,
        "qzone": False,
        "friend_accept": False,
        "guest_mode": "allowlist",
        "guest_ids": [],
        "friend_daily_limit": 0,
        "guest_daily_messages": 50,
        "contact_daily_messages": {},
        "daily_chat_messages": 500,
        "proactive_min": 1,
        "proactive_max": 5,
        "qzone_likes": True,
        "qzone_friends": True,
        "image_understanding": False,
        "voice_understanding": False,
        "voice_replies": False,
        "voice_mode": "adaptive",
        "voice_style": "everyday",
        "startup_welcome": False,
        "singing_enabled": True,
        "game_mode": False,
        "game_mode_auto": False,
        "send_memes": False,
        "autonomous_posts": False,
        "owner_public_sharing": False,
        "post_image_probability": 0.35,
        "offline_recovery": False,
        "qzone_reply_own": False,
        "group_ids": [],
        "group_enabled": False,
        "group_interjections": False,
        "daily_calls": 150,
        "daily_cny": 3.0,
        "monthly_cny": 60.0,
        "validation_daily_calls": 30,
        "validation_daily_cny": 0.5,
        "revision": 1,
    }
    if path.exists():
        defaults.update(json.loads(path.read_text(encoding="utf-8")))
    return defaults


def crypt_key(value, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buf = ctypes.create_string_buffer(value)
    src = Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    crypto = ctypes.windll.crypt32
    if decrypt:
        ok = crypto.CryptUnprotectData(
            ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)
        )
    else:
        ok = crypto.CryptProtectData(
            ctypes.byref(src), "QQCompanion", None, None, None, 1, ctypes.byref(dst)
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.data)


def save_key(key, *, relay=False):
    target = ROOT / "config" / ("relay-api-key.dpapi" if relay else "api-key.dpapi")
    target.parent.mkdir(exist_ok=True)
    temp = target.with_suffix(".new")
    temp.write_bytes(crypt_key(key.encode("utf-8")))
    os.replace(temp, target)


def load_key(*, relay=False):
    p = ROOT / "config" / ("relay-api-key.dpapi" if relay else "api-key.dpapi")
    return crypt_key(p.read_bytes(), decrypt=True).decode("utf-8") if p.exists() else ""


def local_token(name):
    p = ROOT / "config" / (name + ".token")
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(secrets.token_urlsafe(32), encoding="ascii")
    return p.read_text(encoding="ascii").strip()
