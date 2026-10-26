"""Local audition versions; adoption preserves the previous finished cover."""

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import psutil

from settings import ROOT, atomic_json
from singing_resource import active_singing_job
from song_library import SongLibrary
from song_voice import singing_voice


class SongPreviews:
    def __init__(self, db, root=ROOT):
        self.db, self.root = db, root
        self.library = SongLibrary(db, root)

    def folder(self, song_id, version):
        self.library.audio_path(song_id)
        self.library.audio_path(version)
        return self.library.directory / 'previews' / song_id / version

    def alive(self, pid):
        try:
            if not pid:
                return False
            process = psutil.Process(pid)
            expected = (self.root / 'app/song_worker.py').resolve()
            return process.is_running() and any(
                Path(arg).resolve() == expected
                for arg in process.cmdline()[1:] if not arg.startswith('-'))
        except (psutil.Error, OSError, ValueError):
            return False

    def list(self, song_id):
        self.library.audio_path(song_id)
        rows = []
        for path in (self.library.directory / 'previews' / song_id).glob('*/job.json'):
            folder = self.folder(song_id, path.parent.name)
            job = json.loads(path.read_text(encoding='utf-8'))
            result_path = folder / 'result.json'
            result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
            progress_path = folder / 'progress.json'
            progress = json.loads(progress_path.read_text(encoding='utf-8')) if progress_path.is_file() else {}
            rows.append(dict(id=folder.name, song_id=song_id, created=job.get('created', path.stat().st_mtime),
                             label=job.get('label', '调整试听'), state=result.get('state', 'processing' if self.alive(job.get('pid')) else 'failed'),
                             progress=result.get('error') or progress.get('text', ''),
                             pitch_mode=job.get('voice_options', {}).get('singing_pitch_mode', 'preserve'),
                             shift=job.get('voice_options', {}).get('singing_pitch_shift', 0)))
        return sorted(rows, key=lambda r: r['created'], reverse=True)

    def create(self, song_id, cfg, mode='reference_octave', shift=0):
        song = self.library.get(song_id)
        if not song or song['state'] != 'ready' or not self.library.allowed_file(song_id, song['file']):
            raise ValueError('请先选择已有成品的歌曲。')
        if cfg.get('game_mode') or cfg.get('paused') or not cfg.get('live') or not cfg.get('singing_enabled', True):
            raise ValueError('请在恢复在线、退出游戏模式并开启唱歌后制作试听。')
        if mode not in {'reference_octave', 'preserve'} or type(shift) is not int or not -12 <= shift <= 12:
            raise ValueError('音高参数无效。')
        if active_singing_job() or self.db.execute("SELECT 1 FROM songs WHERE state='processing'").fetchone():
            raise ValueError('已有歌曲正在制作，请完成后再试。')
        if any(v['state'] == 'processing' for s in self.library.list() for v in self.list(s['id'])):
            raise ValueError('已有试听正在制作，请完成后再试。')
        version = uuid.uuid4().hex
        folder = self.folder(song_id, version)
        folder.mkdir(parents=True)
        job = dict(song, created=time.time(), label='参考音域试听' if mode == 'reference_octave' else '原音域试听',
                   voice_options=dict(singing_pitch_mode=mode, singing_pitch_shift=shift,
                                      singing_reference=singing_voice(cfg, self.root)['reference']))
        atomic_json(folder / 'job.json', job)
        with (self.root / 'logs/songbook.log').open('ab') as log:
            process = subprocess.Popen([str(self.root.parent / 'SeedVC/.venv/Scripts/python.exe'), '-X', 'utf8',
                str(self.root / 'app/song_worker.py'), '--song-id', song_id, '--preview-id', version],
                cwd=self.root.parent / 'SeedVC', stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        atomic_json(folder / 'job.json', dict(job, pid=process.pid))
        return version

    def ready_file(self, song_id, version):
        folder = self.folder(song_id, version)
        path = folder / 'result.json'
        if not path.is_file():
            raise ValueError('这个试听还没有准备好。')
        result = json.loads(path.read_text(encoding='utf-8'))
        expected = (folder / 'song.mp3').resolve()
        if result.get('state') != 'ready' or result.get('file') != str(expected) or not expected.is_file():
            raise ValueError('这个试听没有可读取的完整音频。')
        return expected, result

    def adopt(self, song_id, version):
        source, result = self.ready_file(song_id, version)
        song = self.library.get(song_id)
        if not song or not self.library.allowed_file(song_id, song['file']):
            raise ValueError('原歌曲已不可用，不能替换。')
        old_version = uuid.uuid4().hex
        archive = self.folder(song_id, old_version)
        archive.mkdir(parents=True)
        old = archive / 'song.mp3'
        shutil.copy2(song['file'], old)
        old_master = Path(song['file']).with_suffix('.flac')
        if old_master.is_file():
            shutil.copy2(old_master, archive / 'song.flac')
        atomic_json(archive / 'job.json', dict(label='替换前的成品', created=time.time()))
        atomic_json(archive / 'result.json', dict(state='ready', file=str(old.resolve()), duration=song['duration']))
        destination = self.library.audio_path(song_id)
        pending = destination.with_suffix('.new.mp3')
        shutil.copy2(source, pending)
        os.replace(pending, destination)
        master = source.with_suffix('.flac')
        if master.is_file():
            shutil.copy2(master, destination.with_suffix('.new.flac'))
            os.replace(destination.with_suffix('.new.flac'), destination.with_suffix('.flac'))
        else:
            destination.with_suffix('.flac').unlink(missing_ok=True)
        self.db.execute("UPDATE songs SET state='ready',duration=?,bytes=?,progress='已采用调整试听',updated=? WHERE id=?",
                        (result['duration'], destination.stat().st_size, time.time(), song_id))
        return dict(adopted=version, previous=old_version)
