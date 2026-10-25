"""Download, separate, convert and mix one complete cover in the Seed-VC environment."""

import argparse
import gc
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

from settings import ROOT, atomic_json, read_settings
from song_library import SongLibrary
from song_source import resolve, download, SourceTemporary, SourceNeeded

SEED = ROOT.parent / "SeedVC"


def ffmpeg():
    candidates = sorted((ROOT.parent / "GPTSoVITS/app").glob("*/runtime/ffmpeg.exe"))
    if not candidates:
        raise RuntimeError("本机音频转换工具不可用。")
    return str(candidates[0])


class SongWorker:
    def __init__(self, song_id):
        self.song_id = song_id
        self.library_root = ROOT / "data/songbook"
        # Validate identifiers before constructing output or cleanup paths.
        self.output = SongLibrary(None).audio_path(song_id).resolve()
        self.work = self.library_root / "work" / song_id
        self.work.mkdir(parents=True, exist_ok=True)
        self.metadata = json.loads((self.work / "job.json").read_text(encoding="utf-8"))
        self.lease = self.library_root / "gpu-job.json"
        self.child = None

    def cancelled(self):
        cfg = read_settings()
        if cfg.get("game_mode") or cfg.get("paused") or not cfg.get("live") or not cfg.get("singing_enabled", True):
            return True
        with sqlite3.connect((ROOT / "data/companion.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT state FROM songs WHERE id=?", (self.song_id,)).fetchone()
        return not row or row[0] == "deleted"

    def progress(self, text):
        atomic_json(self.work / "progress.json", dict(text=text, updated=time.time()))
        print(text, flush=True)

    def command(self, args):
        self.child = subprocess.Popen(args, cwd=SEED, creationflags=subprocess.CREATE_NO_WINDOW)
        started = time.monotonic()
        try:
            while self.child.poll() is None:
                if self.cancelled():
                    raise InterruptedError("歌曲准备已暂停或取消。")
                if time.monotonic() - started > 1800:
                    raise TimeoutError("这首歌准备时间过长，本次没有完成。")
                time.sleep(1)
            if self.child.returncode:
                raise RuntimeError("音频处理没有完成，请查看曲库中的状态后重试。")
        finally:
            if self.child.poll() is None:
                self.child.terminate()
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.kill()
                    self.child.wait()
            self.child = None

    def acquire_gpu(self):
        self.progress("等待释放语音显存")
        atomic_json(self.lease, dict(pid=os.getpid(), song_id=self.song_id, started=time.time()))
        for _ in range(90):
            if self.cancelled():
                raise InterruptedError("歌曲准备已暂停或取消。")
            try:
                voice = json.loads((ROOT / "data/voice-service.json").read_text(encoding="utf-8"))
                if voice.get("state") in {"singing", "disabled", "stopped"} and time.time() - voice.get("updated", 0) < 15:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise TimeoutError("没有取得歌曲制作所需的空闲显存，稍后再试。")

    def separate(self, original):
        import numpy as np
        import soundfile as sf
        import torch
        from demucs.pretrained import get_model
        from demucs.apply import apply_model

        torch.hub.set_dir(str(SEED / "checkpoints/torch"))
        model = get_model("htdemucs").to("cuda").eval()
        samples, rate = sf.read(str(original), dtype="float32", always_2d=True)
        if rate != model.samplerate:
            raise ValueError("音频采样率与分离模型不一致。")
        wav = torch.from_numpy(samples.T)
        ref = wav.mean(0)
        mean, std = ref.mean(), ref.std().clamp_min(1e-6)
        with torch.inference_mode():
            sources = apply_model(model, ((wav - mean) / std)[None], device="cuda", shifts=1, split=True,
                                  overlap=0.25, progress=True, num_workers=0)[0].cpu()
        sources = sources * std + mean
        vocals = sources[model.sources.index("vocals")].numpy().T
        backing = sources.sum(0).numpy().T - vocals
        sf.write(str(self.work / "vocals.wav"), np.mean(vocals, axis=1), rate, subtype="PCM_16")
        sf.write(str(self.work / "backing.wav"), backing, rate, subtype="PCM_16")
        del model, sources, wav, ref, samples
        gc.collect()
        torch.cuda.empty_cache()

    def mix(self, converted):
        import numpy as np
        import soundfile as sf

        voice, rate = sf.read(str(converted), dtype="float32", always_2d=True)
        source, _ = sf.read(str(self.work / "vocals.wav"), dtype="float32", always_2d=True)
        backing, backing_rate = sf.read(str(self.work / "backing.wav"), dtype="float32", always_2d=True)
        if rate != backing_rate:
            raise ValueError("转换后音频采样率不一致。")
        gain = float(np.sqrt(np.mean(source ** 2)) / max(float(np.sqrt(np.mean(voice ** 2))), 1e-6))
        gain = min(3.0, max(0.2, gain))
        count = max(len(voice), len(backing))
        mixed = np.zeros((count, 2), dtype="float32")
        mixed[:len(backing)] += backing
        mixed[:len(voice)] += voice.mean(axis=1, keepdims=True) * gain
        peak = float(np.max(np.abs(mixed)))
        if peak > 0.98:
            mixed *= 0.98 / peak
        sf.write(str(self.work / "mix.wav"), mixed, rate, subtype="PCM_16")
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.command([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.work / "mix.wav"),
                      "-codec:a", "libmp3lame", "-b:a", "192k", str(self.output)])
        return count / rate

    def run(self):
        try:
            self.progress("查找完整音源")
            source = resolve(self.metadata["title"], self.metadata["artist"], self.metadata.get("source_url", ""),
                             verify_source=self.metadata.get("verify_source", False))
            self.progress("读取歌曲音轨")
            download(source, self.work / "source.m4a", self.cancelled)
            self.command([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.work / "source.m4a"),
                          "-vn", "-ar", "44100", "-ac", "2", str(self.work / "original.wav")])
            self.acquire_gpu()
            self.progress("分离人声与伴奏")
            self.separate(self.work / "original.wav")
            if self.cancelled():
                raise InterruptedError("歌曲准备已暂停或取消。")
            self.progress("用大肥鱼的音色学唱")
            self.command([sys.executable, "-X", "utf8", "-u", str(SEED / "companion_svc.py"), "--source", str(self.work / "vocals.wav"),
                          "--output", str(self.work / "converted")])
            converted = list((self.work / "converted").glob("*.wav"))
            if len(converted) != 1:
                raise RuntimeError("没有得到完整歌声，不能收录。")
            self.progress("混合伴奏并保存成品")
            duration = self.mix(converted[0])
            if abs(duration - source["duration"]) > 5 or self.output.stat().st_size < 10000 or self.cancelled():
                raise ValueError("歌曲未完整生成或任务已取消，没有收录。")
            atomic_json(self.work / "result.json", dict(state="ready", file=str(self.output), duration=duration,
                        bytes=self.output.stat().st_size, source_url=source["url"], source_title=source["title"]))
            self.progress("已准备好")
        except Exception as exc:
            self.output.unlink(missing_ok=True)
            safe_error = (str(exc)[:300] if isinstance(exc, (ValueError, InterruptedError, TimeoutError))
                          else "本机没能完成这首歌的制作，请稍后再试或换一个音源。")
            state = ("paused" if isinstance(exc, InterruptedError) else "retry" if isinstance(exc, SourceTemporary)
                     else "needs_source" if isinstance(exc, SourceNeeded) else "failed")
            atomic_json(self.work / "result.json", dict(state=state, error=safe_error, error_type=type(exc).__name__,
                                                       stage=getattr(exc, "stage", "source")))
            raise
        finally:
            try:
                lease = json.loads(self.lease.read_text(encoding="utf-8"))
                if lease.get("pid") == os.getpid():
                    self.lease.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--song-id", required=True)
    args = parser.parse_args()
    os.chdir(SEED)
    for key, value in urllib.request.getproxies().items():
        if key in {"http", "https"}:
            os.environ.setdefault(key.upper() + "_PROXY", value)
    os.environ.setdefault("HF_HOME", str(SEED / "checkpoints/hf_home"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("NUMBA_CACHE_DIR", str(SEED / "cache/numba"))
    SongWorker(args.song_id).run()
