"""One installation-owned TTS lifetime, independent of QQ login and window lifetime."""

import asyncio
import ctypes
import json
import os
import threading
import time

from game_mode import GameMonitor, close_local_voice
from local_voice import LocalVoice
from settings import ROOT, atomic_json, read_settings
from voice_status import voice_required, game_release_message
from singing_resource import active_singing_job

WARM_TIMEOUT = 180


class VoiceService:
    def __init__(self, voice=None, config_reader=read_settings):
        self.voice = voice or LocalVoice(None, config_reader)
        self.config_reader = config_reader
        self.task = None
        self.state = "starting"
        self.since = time.time()
        self.seconds = None
        self.retry_at = 0
        self.active = False

    def status(self, state, message):
        self.state = state
        atomic_json(ROOT / "data/voice-service.json", dict(
            pid=os.getpid(), updated=time.time(), state=state, message=message,
            since=self.since, seconds=self.seconds,
        ))

    async def release(self):
        self.voice.close()
        if self.active:
            await asyncio.to_thread(close_local_voice, tts_only=True)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.active = False

    async def warm(self):
        try:
            success = await asyncio.wait_for(self.voice.prewarm(), WARM_TIMEOUT)
            if not success:
                self.retry_at = time.time() + 60
        except TimeoutError:
            self.voice.close()
            await asyncio.to_thread(close_local_voice, tts_only=True)
            self.active = False
            self.retry_at = time.time() + 60

    async def tick(self):
        cfg = self.config_reader()
        if active_singing_job() and cfg.get("live") and not cfg.get("game_mode"):
            await self.release()
            self.status("singing", "正在准备新歌曲，完成后自动恢复说话语音；文字聊天照常。")
            return
        if not voice_required(cfg):
            if (cfg.get("paused") and cfg.get("voice_replies") and cfg.get("live")
                    and not cfg.get("game_mode") and self.voice.warmed
                    and (self.voice.process is None or self.voice.process.poll() is None)):
                self.status("paused", "聊天已暂停，已准备好的声音继续保留。")
                return
            await self.release()
            state = "game_mode" if cfg.get("game_mode") else "paused" if cfg.get("paused") else "disabled"
            self.status(state, game_release_message() if state == "game_mode" else "语音当前已暂停。")
            return
        if self.task and self.task.done():
            await self.task
            self.task = None
            if self.voice.warmed:
                self.seconds = round(time.time() - self.since, 1)
        if self.voice.warmed:
            process = self.voice.process
            if process is None or process.poll() is None:
                self.status("ready", "声音已准备好，将持续保留到停止大肥鱼。")
                return
            self.voice.close()
        if self.task is None:
            if cfg.get("voice_lazy"):
                try:
                    demand = json.loads((ROOT / "data/voice-demand.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    demand = {}
                if demand.get("requested", 0) <= cfg.get("voice_demand_after", 0):
                    self.status("idle", "语音按需加载，下次需要语音时准备声音。")
                    return
            if time.time() < self.retry_at:
                self.status("unavailable", "语音暂未准备成功，先用文字聊天，稍后自动重试。")
                return
            self.since = time.time()
            self.seconds = None
            self.active = True
            self.task = asyncio.create_task(self.warm())
        self.status("warming", "正在准备声音，与 QQ 登录同步进行。")

    async def run(self):
        # The existing lightweight voice supervisor also owns game detection.
        # Detect before any model warmup, including games running at startup.
        monitor = GameMonitor()
        await asyncio.to_thread(monitor.tick_safely)
        monitor_stop = threading.Event()
        monitor_thread = threading.Thread(target=monitor.run, args=(monitor_stop,), daemon=True)
        monitor_thread.start()
        # Claim the old installation-owned API before warming, including an
        # orphan left by an earlier bot crash. The mutex prevents a second owner.
        try:
            await asyncio.to_thread(close_local_voice, tts_only=True)
            while True:
                await self.tick()
                await asyncio.sleep(2)
        finally:
            monitor_stop.set()
            await asyncio.to_thread(monitor_thread.join, 5)
            await self.release()
            self.status("stopped", "语音服务已停止。")


def main():
    if (ROOT / "config/offline-development.json").exists():
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, True, "Local\\QQCompanionVoice")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return
    try:
        asyncio.run(VoiceService().run())
    finally:
        kernel.ReleaseMutex(ctypes.c_void_p(handle))
        kernel.CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    main()
