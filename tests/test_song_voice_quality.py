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
    assert singing_voice(dict(voice_style='other'), root)['reference'] == str(b.resolve())
    result = singing_voice(dict(singing_reference=str(a), voice_style='other', singing_guidance=0.85), root)
    assert result['reference'] == str(a.resolve()) and result['guidance'] == 0.85


@pytest.mark.parametrize('value', [True, -0.1, 1.1, float('inf'), float('nan'), '0.9'])
def test_invalid_guidance_is_rejected_before_launching_worker(tmp_path, value):
    reference = tmp_path / 'a.wav'
    reference.write_bytes(b'local audio')
    with pytest.raises(ValueError, match='引导强度'):
        singing_voice(dict(singing_reference=str(reference), singing_guidance=value), tmp_path)
