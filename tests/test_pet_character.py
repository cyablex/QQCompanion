# ruff: noqa: E402
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prepare_pet_character import EMOTES, dominant_bounds, prepare


def test_character_conversion_removes_video_border_and_keeps_complete_motion(tmp_path):
    assets = tmp_path / 'assets/memes'
    assets.mkdir(parents=True)
    frames = []
    for x in [32, 36, 32]:
        frame = Image.new('RGBA', (100, 100))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((x, 12, x + 30, 88), fill='#5588ff')
        draw.line((98, 0, 98, 99), fill='white')
        frames.append(frame)
    items = []
    for ident in set(EMOTES.values()):
        file = assets / (ident + '.gif')
        frames[0].save(file, save_all=True, append_images=frames[1:], duration=80, loop=0, disposal=2)
        items.append(dict(id=ident, file=str(file)))
    (assets / 'catalog.json').write_text(json.dumps(items), encoding='utf-8')
    target = prepare(tmp_path)
    assert target and (target / 'character.json').is_file()
    assert not (tmp_path / 'config/vpet.json').exists()
    assert 'graph#Raised_Dynamic' in (target / 'frames/info.lps').read_text(encoding='utf-8')
    assert 'walk.right' in (target / 'frames/info.lps').read_text(encoding='utf-8')
    rendered = list((target / 'frames/idle/Single').glob('*.png'))
    assert len(rendered) == 3
    with Image.open(rendered[0]) as picture:
        assert picture.size == (500, 500)
        bounds = picture.getbbox()
        assert bounds[2] < 450 and bounds[3] <= 480
        assert picture.getpixel((0, 0))[3] == 0
    assert prepare(tmp_path) == target


def test_missing_private_emotes_leave_default_character_available(tmp_path):
    assert prepare(tmp_path) is None
    assert not (tmp_path / 'vpet').exists()


def test_dominant_alpha_component_is_character_not_border():
    frame = Image.new('RGBA', (100, 100))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((30, 10, 70, 90), fill='blue')
    draw.line((99, 0, 99, 99), fill='white')
    assert dominant_bounds(frame) == (30, 10, 71, 91)
