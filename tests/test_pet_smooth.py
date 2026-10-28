import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_pet_smooth import cadence_samples, output_frame_count, restore_alpha, sample_indices  # noqa: E402


def test_smoothing_keeps_the_selected_speed_and_duration():
    kept = output_frame_count(241, 24, 60, 1.35)
    assert kept == 447
    assert abs(kept/60 - 241/24/1.35) < 1/60
    assert output_frame_count(108, 24, 60, 1.35) == 200


@pytest.mark.parametrize('fps,speed', [(24, 1.35), (60, 0), (60, 3)])
def test_invalid_smoothing_rate_is_rejected(fps, speed):
    with pytest.raises(ValueError):
        output_frame_count(241, 24, fps, speed)


def test_dual_matte_preserves_opaque_colour_and_empty_pixels():
    image = Image.new('RGBA', (3, 1))
    image.putdata([(40, 80, 160, 255), (40, 80, 160, 128), (0, 0, 0, 0)])
    black = Image.alpha_composite(Image.new('RGBA', image.size, (0, 0, 0, 255)), image).convert('RGB')
    white = Image.alpha_composite(Image.new('RGBA', image.size, (255, 255, 255, 255)), image).convert('RGB')
    restored = np.asarray(restore_alpha(black, white), dtype=int)
    original = np.asarray(image, dtype=int)
    assert np.max(np.abs(restored-original)) <= 1


def test_periodic_duplicate_cadence_is_interpolated_on_original_time_axis():
    values = [i//3*2 + (i % 3 == 2) for i in range(36)]
    frames = [Image.new('RGBA', (8, 8), (int(v)*5, 80, 100, 255)) for v in values]
    keep, positions = cadence_samples(frames)
    assert len(keep) == 24
    assert positions[0] == .5 and positions[-1] == 35
    assert list(positions[:4]) == [.5, 2, 3.5, 5]


def test_intentional_rest_is_kept_instead_of_compressing_its_duration():
    frames = [Image.new('RGBA', (8, 8), (40, 80, 100, 255)) for _ in range(36)]
    keep, positions = cadence_samples(frames)
    assert keep == list(range(36))
    assert list(positions) == list(range(36))


def test_resampling_preserves_source_time_without_uniformly_retiming_kept_frames():
    indices = sample_indices(np.array([0., 1., 3., 4.]), 9, 24, 48, 1)
    # The removed duplicate at t=2 is crossed halfway between t=1 and t=3.
    assert list(indices) == [0, 2, 4, 5, 6, 7, 8, 10, 12]
