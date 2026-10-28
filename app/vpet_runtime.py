"""Manage only this installation's VPet Core host and its local preferences."""

import json
import os
import subprocess
import time
from pathlib import Path

import psutil

from settings import ROOT, atomic_json, read_settings


def preferences(root=ROOT):
    try:
        return json.loads((Path(root) / 'config/vpet.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(enabled=False)


def executable(root=ROOT):
    return Path(root).resolve() / 'vpet/host/DafeiyuPet.exe'


def processes(root=ROOT):
    target = str(executable(root)).casefold()
    found = []
    for process in psutil.process_iter(['exe']):
        try:
            if str(Path(process.info['exe'] or '').resolve()).casefold() == target:
                found.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return found


def stop(root=ROOT):
    stopped, denied = [], []
    for process in processes(root):
        try:
            process.terminate()
            try:
                process.wait(timeout=3)
            except psutil.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            stopped.append(process.pid)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied:
            denied.append(process.pid)
    return dict(stopped=stopped, denied=denied)


def status(root=ROOT, cfg=None):
    cfg = read_settings() if cfg is None else cfg
    active = processes(root)
    memory = 0
    for process in active:
        try:
            memory += process.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return dict(prepared=executable(root).is_file(), enabled=preferences(root).get('enabled') is True,
                running=bool(active), game_mode=bool(cfg.get('game_mode')),
                memory_mib=round(memory / 1048576, 1))


def reconcile(root=ROOT, cfg=None):
    root = Path(root).resolve()
    cfg = read_settings() if cfg is None else cfg
    if cfg.get('game_mode') or not preferences(root).get('enabled') or (root / 'config/offline-development.json').exists():
        return stop(root)
    target = executable(root)
    if target.is_file() and not processes(root):
        if not (root / 'config/vpet-bridge.token').is_file():
            return dict(stopped=[], denied=[])
        logs = root / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        dotnet = root / 'runtime/dotnet-sdk'
        if (dotnet / 'dotnet.exe').is_file():
            env['DOTNET_ROOT_X64'] = str(dotnet)
        with (logs / 'vpet.log').open('ab') as log:
            subprocess.Popen([str(target), '--root', str(root)], cwd=target.parent,
                             env=env,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return dict(stopped=[], denied=[])


def set_enabled(enabled, root=ROOT, cfg=None):
    if type(enabled) is not bool:
        raise ValueError('桌宠开关无效。')
    if enabled and not executable(root).is_file():
        raise ValueError('桌宠环境还未准备好，请先运行准备桌宠环境。')
    atomic_json(Path(root) / 'config/vpet.json', dict(enabled=enabled))
    reconcile(root, cfg)
    return status(root, cfg)


def open_console(page, root=ROOT, day=''):
    if page not in {'home', 'creative', 'calendar', 'songbook', 'diary'}:
        raise ValueError('不支持的控制台页面。')
    if day:
        from datetime import datetime
        if page != 'diary' or not isinstance(day, str) or datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
            raise ValueError('日记日期无效。')
    root = Path(root).resolve()
    atomic_json(root / 'data/desktop-navigation.json', dict(page=page, day=day, created=time.time()))
    if os.name == 'nt':
        import ctypes
        user = ctypes.windll.user32
        user.FindWindowW.restype = ctypes.c_void_p
        window = user.FindWindowW(None, '大肥鱼控制台')
        if window:
            user.ShowWindow(ctypes.c_void_p(window), 9)
            user.SetForegroundWindow(ctypes.c_void_p(window))
            return
    subprocess.Popen(['cmd.exe', '/c', str(root / '打开大肥鱼控制台.cmd')], cwd=root,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
