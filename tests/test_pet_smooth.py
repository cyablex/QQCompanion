import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_pet_smooth import frame_plan, restore_alpha  # noqa: E402


def test_smoothing_uses_faster_duration_and_keeps_loop_endpoint_available():
    generated, kept = frame_plan(241, 24, 60, 1.35)
    assert generated == 448 and kept == 447
    assert abs(kept/60 - 241/24/1.35) < 1/60
    assert frame_plan(108, 24, 60, 1.35) == (202, 200)


@pytest.mark.parametrize('fps,speed', [(24, 1.35), (60, 0), (60, 3)])
def test_invalid_smoothing_rate_is_rejected(fps, speed):
    with pytest.raises(ValueError):
        frame_plan(241, 24, fps, speed)


def test_dual_matte_preserves_opaque_colour_and_empty_pixels():
    image = Image.new('RGBA', (3, 1))
    image.putdata([(40, 80, 160, 255), (40, 80, 160, 128), (0, 0, 0, 0)])
    black = Image.alpha_composite(Image.new('RGBA', image.size, (0, 0, 0, 255)), image).convert('RGB')
    white = Image.alpha_composite(Image.new('RGBA', image.size, (255, 255, 255, 255)), image).convert('RGB')
    restored = np.asarray(restore_alpha(black, white), dtype=int)
    original = np.asarray(image, dtype=int)
    assert np.max(np.abs(restored-original)) <= 1
