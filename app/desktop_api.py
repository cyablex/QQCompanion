"""Small native bridge for the local desktop UI; never returns API secrets."""

import base64
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import wave
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import game_mode
import relay_setup
import services
from settings import ROOT, atomic_json, read_settings, save_key
from local_voice import MODES as VOICE_MODES, STYLES as VOICE_STYLES
from voice_status import read_voice_status, voice_required
from song_library import SongLibrary
from budget_breakdown import read_budget, period_totals
from art_gallery import ArtGallery
from creative_projects import CreativeProjects
from experience_calendar import ExperienceCalendar
import vpet_runtime
from song_previews import SongPreviews
from song_voice import singing_voice

VERSION = "V5.6"
TZ = timezone(timedelta(hours=8))
FEATURES = (
    "proactive",
    "friend_proactive",
    "web_search",
    "memory_enabled",
    "digital_activities",
    "autonomous_drawing",
    "autonomous_profile",
    "autonomous_posts",
    "owner_public_sharing",
    "qzone",
    "image_understanding",
    "voice_understanding",
    "voice_replies",
    "startup_welcome",
    "singing_enabled",
    "send_memes",
)
TEXT_FIELDS = ("name", "owner_title", "persona", "owner_qq", "bot_qq", "model")


def result(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return dict(ok=True, data=method(self, *args, **kwargs))
        except ValueError as exc:
            return dict(ok=False, message=str(exc))
        except Exception as exc:
            # Paths, tokens and provider responses must not leak through errors.
            return dict(
                ok=False, message=f"操作未完成（{type(exc).__name__}），请稍后重试。"
            )

    return wrapped


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def valid_qq(value):
    return (
        value.isascii()
        and value.isdigit()
        and 5 <= len(value) <= 20
        and not value.startswith("0")
    )


def key_value(value):
    value = str(value).strip()
    if value and (
        not 12 <= len(value) <= 512 or any(c.isspace() or ord(c) < 32 for c in value)
    ):
        raise ValueError("API Key 格式无效，请检查后再保存。")
    return value


class DesktopAPI:
    def __init__(self):
        self._lock = threading.Lock()
        self._window = None
        self._startup_after = 0.0
        self._voice_wait_since = time.time()

    def _attach_window(self, window):
        self._window = window

    @result
    def get_vpet_status(self):
        return vpet_runtime.status(ROOT, read_settings())

    @result
    def set_vpet_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('桌宠开关无效。')
        cfg = read_settings()
        if enabled:
            if not vpet_runtime.executable(ROOT).is_file():
                raise ValueError('桌宠环境还未准备好，请先运行准备桌宠环境。')
            if (ROOT / 'config/offline-development.json').exists():
                raise ValueError('离线恢复期间不会启用桌宠互动。')
            if not valid_qq(cfg['owner_qq']) or not valid_qq(cfg['bot_qq']):
                raise ValueError('请先设置主号与小号，再启用桌宠。')
            services.start(['bot', 'watchdog'])
        return vpet_runtime.set_enabled(enabled, ROOT, cfg)

    @result
    def pop_navigation(self):
        path = ROOT / 'data/desktop-navigation.json'
        value = read_json(path)
        if path.exists():
            path.unlink()
        return value.get('page') if value.get('page') in {'home', 'creative', 'calendar', 'songbook'} and time.time() - value.get('created', 0) < 30 else None

    @result
    def get_experience_calendar(self, month, query=''):
        with sqlite3.connect((ROOT / 'data/companion.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            return ExperienceCalendar(db, read_settings(), ROOT).month(month, query)

    @result
    def open_experience_link(self, project_id, link_id):
        with sqlite3.connect((ROOT / 'data/companion.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            project = CreativeProjects(db, read_settings(), ROOT).get(str(project_id))
            link = next((item for item in project.get('links', []) if item['id'] == link_id), None)
            if not link:
                raise ValueError('没有找到这条收藏链接。')
            from urllib.parse import urlsplit
            parts = urlsplit(link['url'])
            if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password:
                raise ValueError('这条链接不能打开。')
            os.startfile(link['url'])
            return dict(opened=True)

    @result
    def get_creative_projects(self):
        with sqlite3.connect((ROOT / 'data/companion.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            projects = CreativeProjects(db, read_settings(), ROOT)
            active = projects.active()
            return dict(projects=[projects.view(p) for p in projects.list()], assets=projects.available_assets(),
                        active_id=active['id'] if active else '')

    @result
    def change_creative_project(self, action, data):
        if not isinstance(data, dict):
            raise ValueError('项目内容无效。')
        with self._lock, sqlite3.connect(ROOT / 'data/companion.sqlite3', isolation_level=None, timeout=5) as db:
            db.row_factory = sqlite3.Row
            projects = CreativeProjects(db, read_settings(), ROOT)
            ident = data.get('id', '')
            if action == 'create':
                project = projects.create(data.get('title', ''), data.get('kind', 'song'))
            elif action == 'activate':
                project = projects.activate(ident)
            elif action == 'update':
                project = projects.update(ident, data.get('stage'), data.get('next_step', ''))
            elif action == 'note':
                project = projects.note(ident, data.get('text'))
            elif action == 'attach':
                project = projects.attach(ident, data.get('kind'), data.get('asset_id'), data.get('scope', ''))
            elif action == 'link':
                project = projects.link(ident, data.get('title', ''), data.get('url', ''), data.get('note', ''))
            else:
                raise ValueError('项目操作无效。')
            return projects.view(project)

    @result
    def get_song_previews(self, song_id):
        with sqlite3.connect((ROOT / 'data/companion.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            return SongPreviews(db, ROOT).list(str(song_id))

    @result
    def create_song_preview(self, song_id, mode='reference_octave', shift=0):
        with self._lock, sqlite3.connect(ROOT / 'data/companion.sqlite3', isolation_level=None, timeout=5) as db:
            db.row_factory = sqlite3.Row
            return dict(id=SongPreviews(db, ROOT).create(str(song_id), read_settings(), mode, shift))

    @result
    def song_version_preview(self, song_id, version):
        with sqlite3.connect((ROOT / 'data/companion.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            path, _ = SongPreviews(db, ROOT).ready_file(str(song_id), str(version))
            return 'data:audio/mpeg;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')

    @result
    def adopt_song_preview(self, song_id, version):
        with self._lock, sqlite3.connect(ROOT / 'data/companion.sqlite3', isolation_level=None, timeout=5) as db:
            db.row_factory = sqlite3.Row
            return SongPreviews(db, ROOT).adopt(str(song_id), str(version))

    @result
    def get_singing_settings(self):
        cfg = read_settings()
        voice = singing_voice(cfg, ROOT)
        return dict(label=Path(voice['reference']).name if cfg.get('singing_reference') else '跟随当前说话音色',
                    mode=voice['pitch_mode'], shift=voice['shift'])

    @result
    def save_singing_settings(self, mode, shift, follow_voice=False):
        with self._lock:
            cfg = read_settings()
            cfg.update(singing_pitch_mode=mode, singing_pitch_shift=shift)
            if follow_voice:
                cfg['singing_reference'] = ''
            singing_voice(cfg, ROOT)
            cfg['revision'] += 1
            atomic_json(ROOT / 'config/user.json', cfg)
        return dict(saved=True)

    @result
    def choose_singing_reference(self):
        if self._window is None:
            raise ValueError('请在桌面控制台选择参考音色。')
        import webview

        selected = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=('WAV 音色 (*.wav)',))
        if not selected:
            return dict(selected=False)
        source = Path(selected[0] if isinstance(selected, (list, tuple)) else selected)
        if source.suffix.lower() != '.wav':
            raise ValueError('请选择 WAV 格式参考音色。')
        try:
            with wave.open(str(source)) as audio:
                seconds = audio.getnframes() / audio.getframerate()
        except (wave.Error, EOFError) as exc:
            raise ValueError('参考音色需要可读取的 PCM WAV 音频。') from exc
        if not 3 <= seconds <= 30:
            raise ValueError('请选择3到30秒、清晰且只有一个人声的参考音色。')
        destination = ROOT / 'data/songbook/references' / (uuid.uuid4().hex + '.wav')
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        with self._lock:
            cfg = read_settings()
            cfg.update(singing_reference=str(destination.resolve()), revision=cfg['revision'] + 1)
            atomic_json(ROOT / 'config/user.json', cfg)
        return dict(selected=True, label=source.name)

    @result
    def get_drawings(self, query="", scope="all", page=1):
        path = ROOT / "data/companion.sqlite3"
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            return ArtGallery(db, read_settings()["bot_qq"], ROOT).page(query, scope, page)

    def _drawing(self, ident, scope):
        with sqlite3.connect((ROOT / "data/companion.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            row = ArtGallery(db, read_settings()["bot_qq"], ROOT).get(str(ident), str(scope))
        if not row:
            raise ValueError("这幅作品已不在画册中。")
        return row

    @result
    def drawing_preview(self, ident, scope):
        return ArtGallery.preview(self._drawing(ident, scope))

    @result
    def delete_drawing(self, ident, scope):
        with self._lock:
            with sqlite3.connect(ROOT / "data/companion.sqlite3", isolation_level=None, timeout=5) as db:
                deleted = ArtGallery(db, read_settings()["bot_qq"], ROOT).delete(str(ident), str(scope))
        return dict(deleted=deleted)

    @result
    def save_drawing(self, ident, scope):
        row = self._drawing(ident, scope)
        if self._window is None:
            raise ValueError("请在桌面控制台保存作品。")
        import webview

        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", row["title"]).strip(" .")[:60] or "大肥鱼的画"
        selected = self._window.create_file_dialog(webview.SAVE_DIALOG, save_filename=name + ".png", file_types=("PNG 图片 (*.png)",))
        if not selected:
            return dict(saved=False)
        destination = Path(selected[0] if isinstance(selected, (list, tuple)) else selected)
        if not destination.suffix:
            destination = destination.with_suffix(".png")
        if destination.resolve() != Path(row["file"]).resolve():
            shutil.copyfile(row["file"], destination)
        return dict(saved=True)

    @result
    def get_songs(self):
        path = ROOT / "data/companion.sqlite3"
        if not path.exists():
            return dict(songs=[])
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='songs'").fetchone():
                return dict(songs=[])
            return dict(songs=SongLibrary(db, ROOT).list())

    @result
    def song_preview(self, song_id):
        with sqlite3.connect((ROOT / "data/companion.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            library = SongLibrary(db, ROOT)
            song = library.get(str(song_id))
            if not song or not library.allowed_file(song["id"], song["file"]):
                raise ValueError("这首歌还没有准备好，暂时不能试听。")
            return "data:audio/mpeg;base64," + base64.b64encode(Path(song["file"]).read_bytes()).decode("ascii")

    @result
    def delete_song(self, song_id):
        with self._lock:
            with sqlite3.connect(ROOT / "data/companion.sqlite3", isolation_level=None, timeout=5) as db:
                db.row_factory = sqlite3.Row
                removed = SongLibrary(db, ROOT).delete(str(song_id))
        return dict(deleted=removed)

    def _maximized(self):
        return str(self._window.native.WindowState) == "Maximized"

    @result
    def window_action(self, action):
        if self._window is None:
            raise ValueError("窗口尚未准备好。")
        if action == "minimize":
            self._window.minimize()
        elif action == "maximize":
            if self._maximized():
                self._window.restore()
            else:
                self._window.maximize()
        elif action == "close":
            self._window.destroy()
            return dict(closed=True)
        elif action != "state":
            raise ValueError("不支持的窗口操作。")
        return dict(maximized=self._maximized())

    @result
    def resize_window(self, width, height):
        if self._window is None:
            raise ValueError("窗口尚未准备好。")
        if not self._maximized():
            self._window.resize(
                max(960, min(6000, int(width))), max(680, min(4000, int(height)))
            )
        return dict(maximized=self._maximized())

    @result
    def get_status(self):
        cfg = read_settings()
        processes = services.matching()
        health = read_json(ROOT / "data/health.json")
        startup = read_json(ROOT / "data/startup.json")
        now = time.time()
        fresh = (
            0 <= now - health.get("updated", 0) < 90
            and health.get("updated", 0) >= self._startup_after
            and "bot" in processes
        )
        connected = (
            fresh
            and health.get("qq_connected", False)
            and health.get("transport_ready", False)
        )
        phase = (
            startup.get("phase", "connecting")
            if 0 <= now - startup.get("updated", 0) < 45
            else "status_unavailable"
        )
        if phase in {"waiting_login", "wrong_account", "session_unavailable", "recovery_failed"}:
            connected = False
        if not any(name in processes for name in ("bot", "qq", "watchdog", "login")):
            phase = "stopped"
        elif (ROOT / "config/offline-development.json").exists():
            phase = "offline_development"
        elif connected:
            phase = "paused" if cfg["paused"] or not cfg["live"] else "ready"
        elif phase in {"ready", "paused"}:
            phase = "status_unavailable"
        voice = read_voice_status(cfg, processes.get("voice"), now)
        if voice["state"] == "starting" and now - self._voice_wait_since > 180:
            voice = dict(state="unavailable", ready=False, message="语音暂未启动成功，先用文字聊天，后台会继续恢复。")
        startup_ready = bool(connected and (not voice_required(cfg) or voice["ready"] or voice["state"] in {"unavailable", "singing"}))
        budget = self._budget()
        return dict(
            version=VERSION,
            name=cfg["name"],
            phase=phase,
            connected=bool(connected),
            startup_ready=startup_ready,
            voice=voice,
            paused=cfg["paused"],
            live=cfg["live"],
            game_mode=cfg["game_mode"],
            services={name: name in processes for name in services.SCRIPTS},
            daily_cny=cfg["daily_cny"],
            monthly_cny=cfg["monthly_cny"],
            model_route=cfg["model_route"],
            model=cfg.get("relay_connection", {}).get("model", cfg["model"])
            if cfg["model_route"] == "relay"
            else cfg["model"],
            official_model=cfg["model"],
            budget=budget,
            health_age=round(now - health["updated"])
            if health.get("updated")
            else None,
            messages_today=health.get("chat_messages_today", 0) if fresh else None,
            qr_available=phase == "waiting_login"
            and startup.get("qr_available", False),
            login_scanned=startup.get("login_phase") == "qrcode_scanned",
            budget_exhausted=budget is not None and budget["limit_today"] >= cfg["daily_cny"],
        )

    def _budget(self):
        path = ROOT / "data/companion.sqlite3"
        if not path.exists():
            return None
        moment = datetime.now(TZ)
        try:
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                budget = read_budget(db, moment.strftime("%Y-%m-%d"))
                billing_rows = []
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='call_billing'").fetchone():
                    billing_rows = db.execute(
                        "SELECT c.amount,b.details FROM calls c JOIN call_billing b ON b.id=c.id "
                        "WHERE c.day=? AND c.state IN ('completed','completed_with_error') "
                        "AND NOT (c.kind GLOB 'validation_*' OR c.kind GLOB 'acceptance_*')",
                        (moment.strftime("%Y-%m-%d"),),
                    ).fetchall()
            budget["periods"] = period_totals(billing_rows, budget["usage_estimate"] - budget["billing_sync"]["adjustment"])
            return budget
        except sqlite3.Error:
            return None

    @result
    def get_settings(self):
        cfg = read_settings()
        fields = (
            *TEXT_FIELDS,
            *FEATURES,
            "guest_mode",
            "guest_ids",
            "daily_cny",
            "monthly_cny",
            "revision",
            "voice_mode",
            "voice_style",
        )
        return dict(
            values={k: cfg[k] for k in fields},
            key_saved=(ROOT / "config/api-key.dpapi").exists(),
            relay_key_saved=(ROOT / "config/relay-api-key.dpapi").exists(),
            relay={k: relay_setup.read_relay()[k] for k in ("base_url", "model")},
            active_relay={
                k: cfg.get("relay_connection", {}).get(k, "")
                for k in ("base_url", "model")
            },
            route=cfg["model_route"],
        )

    @result
    def save_settings(self, data):
        with self._lock:
            cfg = read_settings()
            if data.get("revision") != cfg["revision"]:
                raise ValueError("设置已在其他地方更新，请重新加载设置后再保存。")
            values = {k: str(data.get(k, "")).strip() for k in TEXT_FIELDS}
            if (
                not all(valid_qq(values[k]) for k in ("owner_qq", "bot_qq"))
                or values["owner_qq"] == values["bot_qq"]
            ):
                raise ValueError("主号和小号需填写不同的 5–20 位 QQ 数字账号。")
            if (
                any(
                    not values[k] or len(values[k]) > 40
                    for k in ("name", "owner_title")
                )
                or len(values["persona"]) > 4000
            ):
                raise ValueError("名字、称呼或人设长度无效。")
            if (
                not values["model"]
                or len(values["model"]) > 128
                or any(c.isspace() or ord(c) < 32 for c in values["model"])
            ):
                raise ValueError("官方模型名无效。")
            guests = data.get("guest_ids", [])
            if (
                not isinstance(guests, list)
                or len(guests) > 10
                or any(not isinstance(x, str) or not valid_qq(x) for x in guests)
            ):
                raise ValueError("测试好友最多 10 位，请填写有效 QQ 号。")
            if data.get("guest_mode") not in {"open", "allowlist"}:
                raise ValueError("好友范围无效。")
            values.update(
                guest_ids=list(dict.fromkeys(guests)), guest_mode=data["guest_mode"]
            )
            for field in FEATURES:
                if type(data.get(field)) is not bool:
                    raise ValueError("功能开关格式无效。")
                values[field] = data[field]
            for field, choices in (("voice_mode", VOICE_MODES), ("voice_style", VOICE_STYLES)):
                value = data.get(field, cfg[field])
                if value not in choices:
                    raise ValueError("语音模式或音色无效。")
                values[field] = value
            for field in ("daily_cny", "monthly_cny"):
                value = float(data.get(field, 0))
                if not math.isfinite(value) or not 0 < value <= 10000:
                    raise ValueError("费用上限应大于 0，且不超过 10000 元。")
                values[field] = value
            key = key_value(data.get("api_key", ""))
            restart = bool(key) or any(
                values[k] != cfg[k] for k in ("model", "owner_qq", "bot_qq")
            )
            identity_changed = any(cfg[k] != values[k] for k in ("owner_qq", "bot_qq"))
            # Match existing setup behavior: identity changes disable live actions.
            if values["guest_mode"] == "open" and cfg.get("guest_mode") != "open":
                values.update(guests=True, friend_accept=True)
            if identity_changed:
                values.update(
                    live=False,
                    proactive=False,
                    guests=False,
                    friend_accept=False,
                    qzone=False,
                )
            if key:
                save_key(key)
            cfg.update(values, revision=cfg["revision"] + 1)
            atomic_json(ROOT / "config/user.json", cfg)
            return dict(restart_required=restart, identity_changed=identity_changed)

    @result
    def voice_preview(self, style):
        if style == "lively":
            raise ValueError("活泼版已停用，目前只使用日常音色。")
        if style not in {"everyday", "welcome"}:
            raise ValueError("试听音色无效。")
        file = ROOT / "assets/voice" / (style + ".wav")
        if not file.is_file():
            raise ValueError("试听还未准备好。")
        return "data:audio/wav;base64," + base64.b64encode(file.read_bytes()).decode("ascii")

    @result
    def get_startup_welcome(self):
        return dict(enabled=bool(read_settings().get("startup_welcome")))

    @result
    def save_relay(self, data):
        with self._lock:
            relay_setup.save(
                {k: [str(data.get(k, ""))] for k in ("base_url", "model", "relay_key")}
            )
            return dict(
                message="接入资料已保存。新地址、模型需核实后启用；更换 Key 后，重启机器人会加载新 Key。"
            )

    @result
    def set_game_mode(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("游戏模式选项无效。")
        with self._lock:
            outcome = game_mode.set_game_mode(enabled)
        return dict(enabled=enabled, warning=bool(outcome["denied"]))

    @result
    def control(self, action):
        with self._lock:
            cfg = read_settings()
            if (
                action in {"start", "resume", "restart_bot"}
                and (ROOT / "config/offline-development.json").exists()
            ):
                raise ValueError("当前为离线开发模式，请先按原流程确认恢复登录。")
            if action in {"start", "resume", "restart_bot"} and (
                not valid_qq(cfg["owner_qq"]) or not valid_qq(cfg["bot_qq"])
            ):
                raise ValueError("请先在偏好设置中填写主号与小号。")
            if action == "start":
                # Starting processes does not silently undo a deliberate pause.
                if "bot" not in services.matching():
                    self._startup_after = time.time()
                services.start(list(services.SCRIPTS))
            elif action in {"pause", "resume"}:
                cfg.update(paused=action == "pause", revision=cfg["revision"] + 1)
                if action == "resume":
                    cfg["live"] = True
                atomic_json(ROOT / "config/user.json", cfg)
            elif action in {"stop", "restart_bot"}:
                # Stop supervisor first, otherwise it would restart stopped helpers.
                targets = (
                    ["watchdog", "login", "bot", "voice", "qq"]
                    if action == "stop"
                    else ["watchdog", "bot"]
                )
                subprocess.run(
                    [
                        str(ROOT / "runtime/python.exe"),
                        str(ROOT / "app/services.py"),
                        "stop",
                        *targets,
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    check=True,
                    timeout=100,
                )
                if action == "restart_bot":
                    services.start(["bot", "watchdog"])
                else:
                    game_mode.close_local_voice()
                    vpet_runtime.stop(ROOT)
            else:
                raise ValueError("不支持的操作。")
            return dict(action=action)

    @result
    def open_login_page(self):
        services.start(["setup"])
        os.startfile("http://127.0.0.1:8765/startup")
        return dict(opened=True)

    @result
    def get_qr(self):
        state = read_json(ROOT / "data/startup.json")
        if (
            state.get("phase") != "waiting_login"
            or not state.get("qr_available")
            or not 0 <= time.time() - state.get("updated", 0) < 45
        ):
            return None
        path = ROOT / "napcat/cache/qrcode.png"
        return (
            "data:image/png;base64,"
            + base64.b64encode(path.read_bytes()).decode("ascii")
            if path.is_file()
            else None
        )
