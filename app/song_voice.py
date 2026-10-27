"""Resolve the current singing reference without fixing a character or gender."""

import json
import math
from pathlib import Path

from settings import ROOT


def singing_voice(cfg, root=ROOT):
    mode = cfg.get("singing_pitch_mode", "reference_octave")
    shift = cfg.get("singing_pitch_shift", 0)
    if mode not in {"reference_octave", "preserve"} or type(shift) is not int or not -12 <= shift <= 12:
        raise ValueError("歌声音高参数无效。")
    reference = cfg.get("singing_reference", "")
    if not reference:
        presets = Path(root).parent / "GPTSoVITS/reference/yui/presets.json"
        profiles = json.loads(presets.read_text(encoding="utf-8"))["profiles"]
        reference = next(p["ref_audio_path"] for p in profiles if p["id"] == cfg.get("voice_style", "everyday"))
    path = Path(reference).resolve()
    if not path.is_file() or path.suffix.lower() != ".wav":
        raise ValueError("请选择可读取的 WAV 参考音色。")
    guidance = cfg.get('singing_guidance', 0.9)
    if isinstance(guidance, bool) or not isinstance(guidance, (int, float)) or not math.isfinite(guidance) or not 0 <= guidance <= 1:
        raise ValueError('歌声参考引导强度必须在0到1之间。')
    return dict(reference=str(path), pitch_mode=mode, shift=shift, steps=50, guidance=float(guidance))
