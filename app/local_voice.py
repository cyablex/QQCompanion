"""Local reference-voice synthesis; text remains the delivery fallback."""

import asyncio
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

import psutil

from settings import ROOT, atomic_json, read_settings

TTS_ROOT = ROOT.parent / "GPTSoVITS"
API = "http://127.0.0.1:9880"
STYLES = {"everyday"}
MODES = {"adaptive", "requested", "always"}
VOICE_MARK = re.compile(r"(?m)^[ \t]*\[语音\][ \t]*|[ \t]*\[语音\]\s*\Z")
SYNTHESIS_WAIT_SECONDS = 12


def synthesis_text(text, owner_title="主人"):
    text = re.sub(r"\s+", " ", text).strip()
    # A very short vocative followed by a comma makes the upstream preprocessor
    # prepend a full stop. Keep the greeting together instead.
    if owner_title:
        text = re.sub(r"^" + re.escape(owner_title) + r"[，,]\s*", owner_title, text)
    # Keep the sentence boundaries used by the approved Chinese-reference preview.
    # cut0 still synthesizes a short turn together without replacing full stops.
    return text


def spoken_text(text):
    text = re.sub(r"\[(?:QQ表情|表情|画画|心情):[^\]]*\]", "", text)
    text = re.sub(r"[*#`]", "", text).strip()
    # Links and long technical replies are clearer as text.
    if not text or len(text) > 160 or re.search(r"https?://|file://", text):
        return ""
    return text


def wants_voice(cfg, contact, latest, selected=False):
    if (not cfg.get("voice_replies") or cfg.get("game_mode")
            or contact != cfg.get("owner_qq")):
        return False
    if re.search(r"(?:不要|不用|别).{0,6}语音|用文字|文字回复", latest):
        return False
    return (cfg.get("voice_mode", "adaptive") == "always"
            or (cfg.get("voice_mode", "adaptive") == "adaptive" and selected) or bool(
        re.search(r"(?:用|发|来|段|条|听|想要|我要).{0,6}语音|语音.{0,6}(?:回|说|读|念)|(?:说|讲|读|念).{0,10}(?:给我听|我听听|让我听|听一下)", latest)
    ))


def allowed_file(cfg, contact, file):
    path = Path(file).resolve()
    return bool(cfg.get("voice_replies") and not cfg.get("game_mode")
                and contact == cfg.get("owner_qq") and path.is_file()
                and path.parent == (ROOT / "data/voice").resolve()
                and path.suffix.lower() == ".wav")


class LocalVoice:
    def __init__(self, store, config_reader=read_settings, managed=False):
        self.store = store
        self.config_reader = config_reader
        self.lock = threading.Lock()
        self.process = None
        self.managed = managed
        self.warmed = False
        self.last_error = None
        self.pending = None
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def close(self):
        self.warmed = False
        process, self.process = self.process, None
        if process and process.poll() is None:
            try:
                parent = psutil.Process(process.pid)
                for child in reversed(parent.children(recursive=True)):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                parent.terminate()
            except psutil.NoSuchProcess:
                pass

    def _ready(self):
        try:
            with self.http.open(API + "/openapi.json", timeout=2) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError):
            return False

    def _synthesize(self, text, style):
        with self.lock:
            # The shared service owns lifetime; idle conversations keep the model loaded.
            return self._run(text, style)

    def _run(self, text, style):
        if style != "everyday":
            raise ValueError("Lively voice has been retired")
        cfg = self.config_reader()
        if cfg.get("game_mode"):
            raise RuntimeError("Voice paused by game mode")
        profiles = json.loads((TTS_ROOT / "reference/yui/presets.json").read_text(encoding="utf-8"))["profiles"]
        profile = next(p for p in profiles if p["id"] == style)
        if not self._ready():
            if self.managed:
                raise RuntimeError("Shared voice service is not ready")
            if not self.process or self.process.poll() is not None:
                with (ROOT / "logs/local-voice.log").open("ab") as log:
                    self.process = subprocess.Popen(
                        [str(ROOT / "runtime/python.exe"), str(TTS_ROOT / "launch.py"), "api"],
                        cwd=TTS_ROOT, stdout=log, stderr=log,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
            deadline = time.monotonic() + 180
            while not self._ready():
                if self.config_reader().get("game_mode"):
                    self.close()
                    raise RuntimeError("Voice paused by game mode")
                if self.process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Local voice startup failed")
                time.sleep(0.5)
        tts_text = synthesis_text(text, cfg.get("owner_title", "主人"))
        body = dict(text=tts_text, text_lang="all_zh", ref_audio_path=profile["ref_audio_path"],
                    prompt_text=profile["prompt_text"], prompt_lang=profile["prompt_lang"],
                    text_split_method="cut0" if len(tts_text) <= 60 else "cut2",
                    fragment_interval=0.12, batch_size=1, media_type="wav",
                    streaming_mode=False, parallel_infer=True, use_cuda_graph=False,
                    sample_steps=4, seed=81, top_k=10, top_p=0.8, temperature=0.8)
        request = urllib.request.Request(API + "/tts", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with self.http.open(request, timeout=240) as response:
            raw = response.read()
        directory = ROOT / "data/voice"
        directory.mkdir(parents=True, exist_ok=True)
        file = directory / (uuid.uuid4().hex + ".wav")
        file.write_bytes(raw)
        with wave.open(str(file), "rb") as audio:
            if audio.getnframes() < audio.getframerate() // 2:
                raise ValueError("Empty synthesized audio")
        return str(file)

    def _start_synthesis(self, text, style):
        task = asyncio.create_task(asyncio.to_thread(self._synthesize, text, style))
        self.pending = task

        def completed(done):
            if not done.cancelled():
                done.exception()
            if self.pending is done:
                self.pending = None

        task.add_done_callback(completed)
        return task

    async def prewarm(self):
        cfg = self.config_reader()
        if (not cfg.get("voice_replies") or cfg.get("game_mode") or cfg.get("paused")
                or not cfg.get("live") or (self.pending and not self.pending.done())):
            return
        if self.warmed and await asyncio.to_thread(self._ready):
            return True
        cfg = self.config_reader()
        if (not cfg.get("voice_replies") or cfg.get("game_mode") or cfg.get("paused")
                or not cfg.get("live") or (self.pending and not self.pending.done())):
            return
        if self.store:
            self.store.set("local_voice_status", dict(state="warming", at=time.time()))
        self.warmed = False
        self.last_error = None
        try:
            # Prepare the actual reference and first inference kernels, without any QQ send.
            await self._start_synthesis("声音准备好了。", "everyday")
            fresh = self.config_reader()
            if (fresh.get("game_mode") or fresh.get("paused") or not fresh.get("live")
                    or not fresh.get("voice_replies")):
                self.close()
                return False
            self.warmed = True
            if self.store:
                self.store.set("local_voice_status", dict(state="warmed", at=time.time()))
            return True
        except asyncio.CancelledError:
            self.close()
            raise
        except Exception as exc:
            self.last_error = type(exc).__name__
            if self.store:
                self.store.set("local_voice_status", dict(state="text_fallback", error=self.last_error, at=time.time()))
            return False

    async def prepare(self, contact, result):
        cfg = self.config_reader()
        users = [m["content"] for m in self.store.history(contact) if m["role"] == "user"]
        if (not result or result.get("action") != "reply" or result.get("drawing_file")
                or not users or not wants_voice(cfg, contact, users[-1], result.get("voice_selected", False))):
            return result
        text = spoken_text("\n".join(result.get("messages", [])))
        if not text:
            return result
        if self.managed:
            from voice_status import read_voice_status

            if not read_voice_status(cfg)["ready"]:
                if cfg.get("voice_lazy"):
                    atomic_json(ROOT / "data/voice-demand.json", dict(requested=time.time()))
                self.store.set("local_voice_status", dict(state="text_fallback", reason="service_preparing", at=time.time()))
                return result
        if self.pending and not self.pending.done():
            self.store.set("local_voice_status", dict(state="text_fallback", reason="synthesis_busy", at=time.time()))
            return result
        style = "everyday"
        source = result.get("read_through", self.store.last_incoming(contact))

        def current():
            fresh = self.config_reader()
            if (fresh.get("revision") != cfg.get("revision") or fresh.get("paused")
                    or not fresh.get("live", True) or fresh.get("game_mode")
                    or not fresh.get("voice_replies") or self.store.last_incoming(contact) != source):
                return False
            if result.get("memory_generation") is not None:
                row = self.store.db.execute("SELECT generation FROM cm_meta WHERE contact=?", (contact,)).fetchone()
                return (row[0] if row else 0) == result["memory_generation"]
            return True

        if not current():
            return result
        try:
            start = time.monotonic()
            task = self._start_synthesis(text, style)
            while not task.done():
                if not current():
                    return result
                remaining = SYNTHESIS_WAIT_SECONDS - (time.monotonic() - start)
                if remaining <= 0:
                    self.store.set("local_voice_status", dict(state="text_fallback", reason="synthesis_slow", at=time.time()))
                    return result
                await asyncio.wait({task}, timeout=min(0.25, remaining))
            if not current():
                return result
            file = task.result()
            self.store.set("local_voice_status", dict(state="ready", style=style, seconds=round(time.monotonic()-start, 1), at=time.time()))
            return dict(result, messages=[text], sticker_id=None, voice_file=file)
        except asyncio.CancelledError:
            self.close()
            raise
        except Exception as exc:
            self.store.set("local_voice_status", dict(state="text_fallback", error=type(exc).__name__, at=time.time()))
            return result
