"""The optional public animation preparation keeps local configuration intact."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import prepare_pet_video as video  # noqa: E402


def test_video_pack_has_attribution_posters_and_fixed_geometry_without_enabling(tmp_path, monkeypatch):
    ffmpeg = tmp_path/'ffmpeg.exe'
    ffmpeg.write_bytes(b'fake')
    calls = []

    def download(url, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'test-only resource')

    def command(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b'test-only converted resource')

    monkeypatch.setattr(video, 'download', download)
    monkeypatch.setattr(video.subprocess, 'run', command)
    result = video.prepare(tmp_path, ffmpeg)
    folder = Path(result['folder'])
    pack = json.loads((folder/'pack.json').read_text())
    assert pack['fps'] == 24 and pack['width'] == pack['height'] == 360
    assert len(pack['clips']) == len(video.CLIPS) == 14
    assert all((folder/e['file']).exists() and (folder/e['poster']).exists() for e in pack['clips'].values())
    assert pack['clips']['lift']['duration'] == 4.5
    assert pack['clips']['idle']['frames'] == 241 and pack['clips']['idle']['duration'] == 241 / 24
    assert any('-lossless' in c for c in calls)  # no lossy conversion of the held segment
    assert 'MIT' in (folder/'SOURCE.txt').read_text()
    assert not (tmp_path/'config/vpet.json').exists()


def test_missing_decoder_does_not_change_existing_pet_setting(tmp_path):
    (tmp_path/'config').mkdir()
    pref = tmp_path/'config/vpet.json'
    pref.write_text('{"enabled":false}')
    with pytest.raises(ValueError, match='FFmpeg'):
        video.prepare(tmp_path, tmp_path/'missing.exe')
    assert pref.read_text() == '{"enabled":false}'
