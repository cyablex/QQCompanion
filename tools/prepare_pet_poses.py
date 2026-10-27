"""Slice optional local high-resolution character sheets for the native host."""

import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def prepare(root=ROOT):
    root = Path(root).resolve()
    source = root / 'assets/pet/hq'
    if not (source / 'daily-sheet.png').is_file():
        return None
    target = root / 'vpet/characters/dafeiyu-v1/poses'
    target.mkdir(parents=True, exist_ok=True)
    rows = []

    def save(sheet, box, name):
        frame = sheet.crop(box).convert('RGBA')
        mask = frame.getchannel('A').point(lambda value: 255 if value > 16 else 0)
        bounds = mask.getbbox()
        if not bounds:
            raise ValueError('Character pose is empty: ' + name)
        cropped = frame.crop(bounds)
        scale = min(880 / cropped.width, 900 / cropped.height)
        cropped = cropped.resize((round(cropped.width * scale), round(cropped.height * scale)), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', (1024, 1024))
        canvas.alpha_composite(cropped, ((1024 - cropped.width) // 2, 974 - cropped.height))
        canvas.save(target / (name + '.png'))
        rows.append(dict(name=name, source_size=frame.size, character_size=cropped.size))

    with Image.open(source / 'daily-sheet.png') as sheet:
        width, height = sheet.size
        # The selected sheet has intact shoes just below the mathematical
        # midpoint; split at the actual transparent gap between its two rows.
        middle = round(height * 525 / 1024)
        for index, name in enumerate(['idle', 'read', 'draw', 'stretch', 'excited', 'eat']):
            column, row = index % 3, index // 3
            save(sheet, (column * width // 3, 0 if row == 0 else middle,
                         (column + 1) * width // 3, middle if row == 0 else height), name)
    walk = source / 'walk-sheet.png'
    if walk.is_file():
        with Image.open(walk) as sheet:
            width, height = sheet.size
            for index, name in enumerate(['walk_left0', 'walk_left1', 'walk_right0', 'walk_right1']):
                column, row = index % 2, index // 2
                save(sheet, (column * width // 2, row * height // 2,
                             (column + 1) * width // 2, (row + 1) * height // 2), name)
    if (source / 'rest.png').is_file():
        with Image.open(source / 'rest.png') as sheet:
            save(sheet, (0, 0, sheet.width, sheet.height), 'rest')
    (target / 'poses.json').write_text(json.dumps(dict(poses=rows, generated_from_local_art=True), indent=2), encoding='utf-8')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    print(prepare(parser.parse_args().root) or 'No optional local high-resolution sheets')
