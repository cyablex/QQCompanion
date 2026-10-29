# ruff: noqa: E402
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from song_voice import singing_voice


def test_future_singing_follows_current_speaking_voice_and_accepts_explicit_reference(tmp_path):
    root = tmp_path / 'Companion'
    folder = root.parent / 'GPTSoVITS/reference/yui'
    folder.mkdir(parents=True)
    a, b = folder / 'a.wav', folder / 'b.wav'
    a.write_bytes(b'audio-a')
    b.write_bytes(b'audio-b')
    (folder / 'presets.json').write_text(json.dumps(dict(profiles=[
        dict(id='everyday', ref_audio_path=str(a)), dict(id='other', ref_audio_path=str(b))])), encoding='utf-8')
    assert singing_voice({}, root)['reference'] == str(a.resolve())
    assert singing_voice({}, root)['pitch_mode'] == 'preserve'
    assert singing_voice(dict(voice_style='other'), root)['reference'] == str(b.resolve())
    result = singing_voice(dict(singing_reference=str(a), voice_style='other', singing_guidance=0.85), root)
    assert result['reference'] == str(a.resolve()) and result['guidance'] == 0.85


def test_singing_reference_can_follow_voice_without_reusing_synthetic_speech(tmp_path):
    root = tmp_path / 'Companion'
    folder = root.parent / 'GPTSoVITS/reference/yui'
    folder.mkdir(parents=True)
    speech, original = folder / 'speech.wav', folder / 'original.wav'
    speech.write_bytes(b'synthesized speaking reference')
    original.write_bytes(b'original voice reference')
    (folder / 'presets.json').write_text(json.dumps(dict(profiles=[dict(id='everyday',
        ref_audio_path=str(speech), singing_ref_audio_path=str(original))])), encoding='utf-8')
    assert singing_voice({}, root)['reference'] == str(original.resolve())
    assert singing_voice(dict(singing_reference=str(speech)), root)['reference'] == str(speech.resolve())


@pytest.mark.parametrize('shift', [0, 3, -2])
def test_explicit_transposition_moves_backing_into_same_key_and_preserves_duration(tmp_path, monkeypatch, shift):
    import numpy as np
    import soundfile as sf
    import song_worker
    from types import SimpleNamespace

    rate, count = 44100, 4410
    worker = song_worker.SongWorker.__new__(song_worker.SongWorker)
    worker.work = tmp_path
    worker.output = tmp_path / 'song.mp3'
    sf.write(tmp_path / 'vocals.wav', np.full(count, .1), rate, subtype='FLOAT')
    sf.write(tmp_path / 'converted.wav', np.full(count, .1), rate, subtype='FLOAT')
    sf.write(tmp_path / 'backing.wav', np.column_stack([np.full(count, .2), np.full(count, .3)]), rate, subtype='FLOAT')
    calls = []

    def pitch_shift(samples, *, sr, n_steps):
        calls.append((samples.shape, sr, n_steps))
        return samples

    monkeypatch.setitem(sys.modules, 'librosa', SimpleNamespace(effects=SimpleNamespace(pitch_shift=pitch_shift)))
    monkeypatch.setattr(song_worker, 'ffmpeg', lambda: 'fake-encoder')
    worker.command = lambda _: None
    assert worker.mix(tmp_path / 'converted.wav', shift=shift) == pytest.approx(count / rate)
    assert calls == ([((2, count), rate, shift)] if shift else [])
    mixed, sr = sf.read(tmp_path / 'song.flac')
    assert sr == rate and mixed.shape == (count, 2)
    assert sf.info(tmp_path / 'song.flac').subtype == 'PCM_24'
    assert np.max(np.abs(mixed)) <= .98


@pytest.mark.parametrize('value', [True, -0.1, 1.1, float('inf'), float('nan'), '0.9'])
def test_invalid_guidance_is_rejected_before_launching_worker(tmp_path, value):
    reference = tmp_path / 'a.wav'
    reference.write_bytes(b'local audio')
    with pytest.raises(ValueError, match='引导强度'):
        singing_voice(dict(singing_reference=str(reference), singing_guidance=value), tmp_path)
