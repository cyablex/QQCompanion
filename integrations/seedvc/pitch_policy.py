"""Match voiced phrases to a reference register using whole octaves only."""

import math
from statistics import median


def reference_register(source, reference, frame_seconds=0.01):
    values = [float(v) for v in source]
    target = [float(v) for v in reference if math.isfinite(float(v)) and float(v) > 50]
    if not target:
        return values, dict(mode="reference_octave", phrases=[], reason="reference_unvoiced")
    target_hz = median(target)
    voiced = [i for i, v in enumerate(values) if math.isfinite(v) and v > 50]
    groups = []
    for i in voiced:
        if not groups or (i - groups[-1][-1]) * frame_seconds > 0.25:
            groups.append([i])
        else:
            groups[-1].append(i)
    phrases = []
    for group in groups:
        hz = median(values[i] for i in group)
        ratio = target_hz / hz
        octave = 1 if ratio > 1.5 else -1 if ratio < 0.6 else 0
        if len(group) * frame_seconds < 0.12:
            octave = 0
        for i in group:
            values[i] *= 2 ** octave
        phrases.append(dict(start=round(group[0] * frame_seconds, 2),
                            end=round((group[-1] + 1) * frame_seconds, 2),
                            source_hz=round(hz, 2), semitones=12 * octave))
    return values, dict(mode="reference_octave", reference_hz=round(target_hz, 2), phrases=phrases)
