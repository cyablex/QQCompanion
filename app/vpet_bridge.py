"""Loopback VPet interactions use the live companion Engine, history and budget."""

import asyncio
import contextvars
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from pathlib import Path
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from settings import ROOT, local_token
from creative_projects import CreativeProjects
from diary_book import DiaryBook
from message_channels import route
from vpet_messages import PetMessages
import vpet_runtime

PORT = 8766
SCENES = {
    'rice_placed': '主人在桌宠旁放了一碗白米饭，开始了桌面喂饭互动。',
    'rice_caught': '在桌面喂饭互动里，大肥鱼追到了主人拿着或放下的白米饭，抱住了饭碗。',
    'rice_finished': '在桌面喂饭互动里，大肥鱼把主人给的白米饭吃完了。',
}
DELIVERY = contextvars.ContextVar('vpet_delivery', default=None)
INTERACTIONS = {'head': '主人在桌宠上摸了摸你的头。', 'body': '主人在桌宠上轻轻戳了你一下。',
                'lift': '主人把桌宠提了起来。'}


@dataclass
class MediaResponse:
    path: Path
    mime: str


class VPetBridge:
    def __init__(self, plugin, root=ROOT):
        self.plugin, self.engine, self.store = plugin, plugin.engine, plugin.store
        self.root = root
        self.server = None
        self.loop = None
        self.last_reply = 0
        self.requests = set()
        self.chat_tasks = set()

    def record_source(self, ident, cfg):
        self.store.set('event_source:' + ident, dict(channel='vpet', owner=cfg['owner_qq'], bot=cfg['bot_qq'], time=time.time()))

    async def route_delivery(self, contact, kind, result, key, index, text, options):
        if DELIVERY.get() is not None:
            return await self.deliver(contact, text, **options)
        cfg = self.engine.config_reader()
        channel = route(self.store, cfg, contact, event_id=result.get('delivery_event'),
                        read_through=result.get('read_through') if kind not in {'proactive', 'friend_proactive'} else None)
        if channel == 'qq':
            return await self.plugin.send_private(contact, text, **options)
        if channel != 'vpet' or cfg.get('game_mode'):
            from engine import DeliveryUnavailable
            raise DeliveryUnavailable('Original desktop channel unavailable')
        if options.get('sticker_id'):
            item = self.engine.stickers.items.get(options['sticker_id']) if self.engine.stickers else None
            if not item:
                from engine import DeliveryUnavailable
                raise DeliveryUnavailable('Sticker unavailable')
            options = dict(options, sticker_file=item['file'])
        ident = PetMessages(self.store, cfg).put(key, index, text, options)
        return dict(message_id='vpet:' + ident)

    async def chat(self, value):
        if (not isinstance(value, dict) or set(value) != {'id', 'text'}
                or not isinstance(value['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['id'])
                or not isinstance(value['text'], str) or not value['text'].strip() or len(value['text']) > 2000):
            raise ValueError('请填写2000字以内的消息。')
        cfg = self.engine.config_reader()
        if cfg.get('game_mode') or not self.engine.allowed(cfg, cfg['owner_qq']) or not vpet_runtime.preferences(self.root).get('enabled'):
            raise ValueError('大肥鱼正在休息，暂时没有接收这条消息。')
        event = 'vpet:' + value['id']
        owner = cfg['owner_qq']
        receipt_key = f"vpet_command:{cfg['bot_qq']}:{owner}:{value['id']}"
        previous = self.store.get(receipt_key)
        if previous:
            return dict(accepted=True, id=value['id'], confirmation=previous['confirmation'])
        if self.store.db.execute('SELECT 1 FROM events WHERE id=?', (event,)).fetchone():
            return dict(accepted=True, id=value['id'])
        command = self.engine.personal_command(owner, value['text'].strip())
        if command is None:
            command = self.engine.admin(owner, value['text'].strip())
        if command is not None:
            # Command acknowledgments use this authenticated HTTP response, even
            # when the command just forgot its own input or paused the engine.
            self.store.set(receipt_key, dict(confirmation=command))
            return dict(accepted=True, id=value['id'], confirmation=command)
        if self.store.incoming(event, owner, value['text'].strip()):
            self.record_source(event, cfg)
            task = self.plugin.enqueue_reply(owner, event)
            self.chat_tasks.add(task)
            task.add_done_callback(self.chat_tasks.discard)
        return dict(accepted=True, id=value['id'])

    async def media(self, ident):
        cfg = self.engine.config_reader()
        if cfg.get('game_mode'):
            raise ValueError('游戏模式下暂停桌面播放。')
        item = PetMessages(self.store, cfg).get(ident)['media']
        if not item:
            raise ValueError('这条消息没有附件。')
        path = Path(item['path'])
        if item['kind'] == 'song':
            valid = self.engine.singing and self.engine.singing.library.allowed_file(item['song_id'], path)
            mime = 'audio/mpeg'
        elif item['kind'] == 'voice':
            from local_voice import allowed_file
            valid = allowed_file(cfg, cfg['owner_qq'], path)
            mime = 'audio/wav'
        elif item['kind'] == 'sticker':
            sticker = self.engine.stickers.items.get(item['sticker_id']) if self.engine.stickers else None
            valid = sticker and Path(sticker['file']).resolve() == path.resolve()
            mime = mimetypes.guess_type(path.name)[0] or 'image/png'
        else:
            valid = self.engine.sketchbook.allowed_file(cfg, path, cfg['owner_qq'])
            mime = 'image/png'
        if not valid or not path.is_file():
            raise ValueError('附件已不可用，文字记录仍保留。')
        return MediaResponse(path, mime)

    def _key(self, cfg, ident):
        return f"vpet:{cfg['bot_qq']}:{cfg['owner_qq']}:{ident}"

    async def state(self):
        cfg = self.engine.config_reader()
        project = CreativeProjects(self.store.db, cfg, self.root).active()
        active = self.store.db.execute(
            "SELECT kind FROM turn_tasks WHERE contact=? AND bot=? AND generation=? AND state IN ('queued','running') "
            "ORDER BY created DESC LIMIT 1", (cfg['owner_qq'], cfg['bot_qq'], self.engine.memory_epoch(cfg['owner_qq']))).fetchone()
        song = self.store.db.execute(
            "SELECT s.title,s.state FROM song_requests r JOIN songs s ON s.id=r.song_id "
            "WHERE r.contact=? AND r.bot=? AND r.generation=? AND r.state='waiting' ORDER BY r.created DESC LIMIT 1",
            (cfg['owner_qq'], cfg['bot_qq'], self.engine.memory_epoch(cfg['owner_qq']))).fetchone()
        activity = ('正在画画' if active and active['kind'].startswith('drawing.')
                    else '正在做你交代的事' if active
                    else f"正在学唱《{song['title']}》" if song and song['state'] == 'processing'
                    else f"《{song['title']}》等待准备或发送" if song
                    else '等你来玩')
        diary = DiaryBook(self.store.db, cfg, self.root).activity()
        if diary.get('phase') == 'writing' and not active and not song:
            activity = '正在写日记'
        return dict(game_mode=bool(cfg.get('game_mode')), enabled=vpet_runtime.preferences(self.root).get('enabled') is True,
                    ready=self.engine.allowed(cfg, cfg['owner_qq']), activity=activity,
                    generation=self.engine.memory_epoch(cfg['owner_qq']),
                    scope=cfg['bot_qq'] + ':' + cfg['owner_qq'],
                    diary=diary,
                    character='大肥鱼·蓝发女仆' if (Path(self.root) / 'vpet/characters/dafeiyu-v1/character.json').is_file() else 'VPet演示形象',
                    project=project['title'] if project else '', next_step=project['next_step'] if project else '')

    async def interact(self, value):
        if (not isinstance(value, dict) or set(value) != {'id', 'kind'}
                or not isinstance(value['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['id'])
                or value['kind'] not in INTERACTIONS):
            raise ValueError('不支持的桌宠互动。')
        cfg = self.engine.config_reader()
        owner = cfg['owner_qq']
        if cfg.get('game_mode') or not vpet_runtime.preferences(self.root).get('enabled') or not self.engine.allowed(cfg, owner):
            return dict(messages=[], saved=False, state='paused')
        # Routine touch is local animation, not a chat turn or memory source.
        return dict(messages=[], saved=False, state='local')

    async def scene(self, value):
        if (not isinstance(value, dict) or set(value) != {'id', 'kind', 'scope', 'generation'}
                or not isinstance(value['id'], str) or not re.fullmatch(r'[a-f0-9]{32}', value['id'])
                or not isinstance(value['kind'], str) or value['kind'] not in SCENES or type(value['generation']) is not int):
            raise ValueError('不支持的桌面场景记录。')
        cfg = self.engine.config_reader()
        owner = cfg['owner_qq']
        if (value['scope'] != cfg['bot_qq'] + ':' + owner or value['generation'] != self.engine.memory_epoch(owner)
                or cfg.get('game_mode') or not self.engine.allowed(cfg, owner)
                or not vpet_runtime.preferences(self.root).get('enabled')):
            return dict(saved=False, state='inactive')
        # Accept older hosts without persisting their high-frequency toy events.
        return dict(saved=False, state='local')

    async def write_diary(self, value):
        from datetime import datetime
        from store import TZ
        if (not isinstance(value, dict) or set(value) != {'day', 'scope', 'generation'}
                or not isinstance(value['day'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value['day'])
                or type(value['generation']) is not int):
            raise ValueError('请选择有效的日记日期。')
        datetime.strptime(value['day'], '%Y-%m-%d')
        if value['day'] > datetime.now(TZ).strftime('%Y-%m-%d'):
            raise ValueError('不能提前写未来的日记。')
        cfg = self.engine.config_reader()
        owner = cfg['owner_qq']
        if (value['scope'] != cfg['bot_qq'] + ':' + owner or value['generation'] != self.engine.memory_epoch(owner)
                or not cfg.get('memory_enabled') or cfg.get('game_mode') or not self.engine.allowed(cfg, owner)):
            raise ValueError('大肥鱼正在休息，或记录已经变化，请刷新后再试。')
        diary = self.plugin.daily_diary
        if diary.lock.locked():
            return dict(accepted=False, state='writing')
        key = f"diary_activity:{cfg['bot_qq']}:{owner}"
        job = secrets.token_hex(16)
        self.store.set(key, dict(job_id=job, phase='queued', day=value['day'], started=time.time(), generation=value['generation']))

        async def draft():
            fresh = self.engine.config_reader()
            if (fresh['owner_qq'] != owner or fresh['bot_qq'] != cfg['bot_qq']
                    or self.engine.memory_epoch(owner) != value['generation']):
                return
            try:
                saved = await diary.write(self.engine, fresh, value['day'])
                current = self.store.get(key, {})
                if not saved and current.get('job_id') == job and self.engine.memory_epoch(owner) == value['generation']:
                    self.store.set(key, dict(current, phase='skipped'))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.set('last_memory_error', type(exc).__name__)
                current = self.store.get(key, {})
                if current.get('job_id') == job:
                    self.store.set(key, dict(current, phase='failed'))

        task = asyncio.create_task(draft())
        self.chat_tasks.add(task)
        task.add_done_callback(self.chat_tasks.discard)
        return dict(accepted=True, state='queued')

    async def deliver(self, contact, text, **media):
        target = DELIVERY.get()
        if target is None:
            return None
        cfg = self.engine.config_reader()
        if contact != cfg['owner_qq'] or cfg.get('game_mode') or not vpet_runtime.preferences(self.root).get('enabled') or media:
            from engine import DeliveryUnavailable
            raise DeliveryUnavailable('Desktop interaction delivery unavailable')
        target['messages'].append(text)
        return dict(message_id=f"vpet:{target['event']}:{len(target['messages'])}")

    async def dispatch(self, method, path, value):
        task = asyncio.current_task()
        self.requests.add(task)
        try:
            return await self._dispatch(method, path, value)
        finally:
            self.requests.discard(task)

    async def _dispatch(self, method, path, value):
        if method == 'GET' and path == '/status':
            return await self.state()
        if method == 'POST' and path == '/interact':
            return await self.interact(value)
        if method == 'POST' and path == '/scene':
            return await self.scene(value)
        if method == 'POST' and path == '/diary':
            return await self.write_diary(value)
        if method == 'POST' and path == '/chat':
            return await self.chat(value)
        if method == 'GET' and path == '/messages':
            cfg = self.engine.config_reader()
            return dict(messages=[] if cfg.get('game_mode') else PetMessages(self.store, cfg).pending())
        if method == 'POST' and path == '/ack':
            return PetMessages(self.store, self.engine.config_reader()).acknowledge(value.get('ids') if isinstance(value, dict) else None)
        if method == 'GET' and path == '/history':
            return dict(messages=self.store.history(self.engine.config_reader()['owner_qq'], limit=40, max_chars=16000))
        if method == 'GET' and path.startswith('/media/'):
            return await self.media(path.removeprefix('/media/'))
        if method == 'POST' and path == '/open' and isinstance(value, dict) and set(value) in ({'page'}, {'page', 'day'}):
            vpet_runtime.open_console(value['page'], self.root, value.get('day', ''))
            return dict(opened=True)
        raise ValueError('不支持的桌宠请求。')

    def start(self):
        self.loop = asyncio.get_running_loop()
        bridge, token = self, local_token('vpet-bridge')

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def handle_request(self):
                if (self.headers.get('Host') != f'127.0.0.1:{PORT}' or self.headers.get('Origin')
                        or not secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token)):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 <= length <= 32768:
                        raise ValueError('请求过长。')
                    value = json.loads(self.rfile.read(length)) if length else None
                    future = asyncio.run_coroutine_threadsafe(bridge.dispatch(self.command, self.path, value), bridge.loop)
                    data = future.result(timeout=180)
                    if isinstance(data, MediaResponse):
                        with data.path.open('rb') as source:
                            self.send_response(200)
                            self.send_header('Content-Type', data.mime)
                            self.send_header('Cache-Control', 'no-store')
                            self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
                            self.end_headers()
                            import shutil
                            try:
                                shutil.copyfileobj(source, self.wfile)
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                        return
                    payload = dict(ok=True, data=data)
                    status = 200
                except ValueError as exc:
                    payload, status = dict(ok=False, message=str(exc)), 400
                except Exception:
                    payload, status = dict(ok=False, message='这次互动没有确认完成，记录不会自动重放。'), 503
                body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = handle_request

        self.server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True, name='vpet-bridge').start()

    async def close(self):
        if self.server:
            await asyncio.to_thread(self.server.shutdown)
            self.server.server_close()
        tasks = list(self.requests | self.chat_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
