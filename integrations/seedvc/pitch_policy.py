"""Choose one upward octave for a whole song, preserving its melodic intervals."""

import math
from statistics import median


def reference_register(source, reference, frame_seconds=0.01):
    values = [float(v) for v in source]
    target = [float(v) for v in reference if math.isfinite(float(v)) and float(v) > 50]
    if not target:
        return values, dict(mode="reference_octave", phrases=[], reason="reference_unvoiced")
    target_hz = median(target)
    voiced = [i for i, v in enumerate(values) if math.isfinite(v) and v > 50]
    source_hz = median(values[i] for i in voiced) if voiced else 0
    # A speaking reference is not a ceiling for sung notes. Never fold a high
    # chorus down, and never choose different octaves for adjacent phrases.
    octave = 1 if source_hz and target_hz / source_hz > 1.5 else 0
    # Do not push real high notes beyond the singing model's 1100 Hz F0 bins.
    # Keep one interval-preserving decision for the entire song.
    high_hz = max((values[i] for i in voiced), default=0)
    limited = bool(octave and high_hz * 2 > 1100)
    if limited:
        octave = 0
    for i in voiced:
        values[i] *= 2 ** octave
    groups = []
    for i in voiced:
        if not groups or (i - groups[-1][-1]) * frame_seconds > 0.25:
            groups.append([i])
        else:
            groups[-1].append(i)
    phrases = []
    for group in groups:
        hz = median(float(source[i]) for i in group)
        phrases.append(dict(start=round(group[0] * frame_seconds, 2),
                            end=round((group[-1] + 1) * frame_seconds, 2),
                            source_hz=round(hz, 2), semitones=12 * octave))
    return values, dict(mode="reference_octave", policy='whole_song_up_only', reference_hz=round(target_hz, 2),
                        source_hz=round(source_hz, 2), semitones=12 * octave, phrases=phrases,
                        high_hz=round(high_hz, 2), limited_by_model_range=limited)


def install_pitch_guard(regulator):
    """Avoid upstream out-of-range F0 bins wrapping to the unvoiced bin."""
    original = regulator.f0_to_coarse

    def bounded(f0, bins):
        return original(f0.clamp(min=0, max=1100.0), bins)

    regulator.f0_to_coarse = bounded
