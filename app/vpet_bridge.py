"""Loopback VPet interactions use the live companion Engine, history and budget."""

import asyncio
import contextvars
import json
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from settings import ROOT, local_token
from creative_projects import CreativeProjects
import vpet_runtime

PORT = 8766
DELIVERY = contextvars.ContextVar('vpet_delivery', default=None)
INTERACTIONS = {'head': '主人在桌宠上摸了摸你的头。', 'body': '主人在桌宠上轻轻戳了你一下。',
                'lift': '主人把桌宠提了起来。'}


class VPetBridge:
    def __init__(self, plugin, root=ROOT):
        self.plugin, self.engine, self.store = plugin, plugin.engine, plugin.store
        self.root = root
        self.server = None
        self.loop = None
        self.last_reply = 0
        self.requests = set()

    def _key(self, cfg, ident):
        return f"vpet:{cfg['bot_qq']}:{cfg['owner_qq']}:{ident}"

    async def state(self):
        cfg = self.engine.config_reader()
        project = CreativeProjects(self.store.db, cfg, self.root).active()
        active = self.store.db.execute(
            "SELECT kind FROM turn_tasks WHERE contact=? AND bot=? AND state IN ('queued','running') "
            "ORDER BY created DESC LIMIT 1", (cfg['owner_qq'], cfg['bot_qq'])).fetchone()
        activity = '正在做你交代的事' if active else '等你来玩'
        return dict(game_mode=bool(cfg.get('game_mode')), enabled=vpet_runtime.preferences(self.root).get('enabled') is True,
                    ready=self.engine.allowed(cfg, cfg['owner_qq']), activity=activity,
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
        key = self._key(cfg, value['id'])
        async with self.engine.contact_locks.setdefault(owner, asyncio.Lock()):
            # Bind the actual local installation's owner, never an identity from the client.
            fresh = self.engine.config_reader()
            if (fresh['owner_qq'] != owner or fresh['bot_qq'] != cfg['bot_qq'] or fresh.get('game_mode')
                    or not self.engine.allowed(fresh, owner) or not vpet_runtime.preferences(self.root).get('enabled')):
                return dict(messages=[], saved=False, state='paused')
            previous = self.store.get(key)
            if previous:
                return previous
            now = time.time()
            # Frequent touches still animate locally; only a bounded interaction reaches the model/history.
            if now - self.last_reply < 30:
                return dict(messages=[], saved=False, state='cooldown')
            self.last_reply = now
            self.store.incoming('vpet:' + value['id'], owner, '[桌宠互动] ' + INTERACTIONS[value['kind']])
            generation = self.engine.memory_epoch(owner)
            pending = dict(messages=[], saved=True, state='recorded')
            self.store.set(key, pending)
            target = []
            token = DELIVERY.set(dict(messages=target, event=value['id']))
            try:
                result = await self.engine.decision(owner, 'pet_interaction',
                    INTERACTIONS[value['kind']] + '这是刚发生的真实桌面互动。用你平常的性格简短回应一两句即可。'
                    '此次只回应互动，不点歌、不画画、不修改资料、不执行任务，也不承诺稍后做事。'
                    '使用结构化回复协议，只输出reply或skip及messages、memory。')
                if result and result.get('action') == 'reply':
                    from turn_actions import TurnActions
                    TurnActions.decode(json.dumps(dict(action='reply', messages=result['messages'], tasks=[])), cfg, result['read_through'])
                    safe = {field: result[field] for field in ('action', 'messages', 'revision', 'read_through', 'memory_generation') if field in result}
                    await self.engine.deliver(owner, 'reply', safe, key=key + ':reply')
                if self.engine.memory_epoch(owner) != generation:
                    return dict(messages=[], saved=False, state='forgotten')
                saved = dict(messages=target, saved=True, state='replied' if target else 'recorded')
                self.store.set(key, saved)
                return saved
            finally:
                DELIVERY.reset(token)

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
        if method == 'POST' and path == '/open' and isinstance(value, dict) and set(value) == {'page'}:
            vpet_runtime.open_console(value['page'], self.root)
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
                    if not 0 <= length <= 8192:
                        raise ValueError('请求过长。')
                    value = json.loads(self.rfile.read(length)) if length else None
                    future = asyncio.run_coroutine_threadsafe(bridge.dispatch(self.command, self.path, value), bridge.loop)
                    data = future.result(timeout=180)
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
        tasks = list(self.requests)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
