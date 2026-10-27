"""Prepare optional local diary illustrations and hand-picked animation sheets."""
import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def prepare(root=ROOT):
    root = Path(root).resolve()
    diary = root / 'assets/diary'
    source = diary / 'source/maid-vignettes-v1.png'
    result = dict(illustrations=0, animations=0)
    if source.is_file():
        target = diary / 'illustrations'
        target.mkdir(parents=True, exist_ok=True)
        with Image.open(source).convert('RGBA') as sheet:
            # Selected production atlas: use gaps between the complete drawings.
            row_cuts = [[0, 540, 1028, 1536], [0, 522, 1065, 1536]]
            ys = [0, round(sheet.height * 535 / 1024), sheet.height]
            for i, name in enumerate(['quiet', 'song', 'reading', 'rice', 'night', 'memory']):
                col, row = i % 3, i // 3
                xs = [round(sheet.width * x / 1536) for x in row_cuts[row]]
                frame = sheet.crop((xs[col], ys[row], xs[col+1], ys[row+1]))
                box = frame.getchannel('A').point(lambda p: 255 if p > 20 else 0).getbbox()
                if not box:
                    raise ValueError('Empty diary illustration: ' + name)
                frame = frame.crop(box)
                frame.thumbnail((700, 600), Image.Resampling.LANCZOS)
                frame.save(target / (name + '.png'))
                result['illustrations'] += 1
    folder = root / 'assets/pet/animation-v511'
    for name, indices, duration in [
        ('read', [0, 1, 3], [2400, 180, 420]),
        ('write', [0, 1, 2, 3], [340, 330, 160, 420]),
        ('eat', [0, 1, 2, 3], [420, 340, 520, 400]),
        ('reaction', list(range(6)), [300] * 6),
        ('settle', [0, 1, 2, 3], [140, 220, 240, 180]),
    ]:
        path = folder / (name + '.png')
        if not path.is_file():
            continue
        with Image.open(path).convert('RGBA') as sheet:
            cols, rows = (3, 2) if name == 'reaction' else (2, 2)
            cuts = [0, round(sheet.height * 576 / 1024), sheet.height] if name == 'settle' else [i * sheet.height // rows for i in range(rows+1)]
            frames = [sheet.crop((i % cols * sheet.width // cols, cuts[i // cols],
                                  (i % cols + 1) * sheet.width // cols, cuts[i // cols + 1])) for i in indices]
        bounds = [f.getchannel('A').point(lambda p: 255 if p > 20 else 0).getbbox() for f in frames]
        if any(b is None for b in bounds):
            raise ValueError('Empty animation frame: ' + name)
        scale = min(440 / max(b[2] - b[0] for b in bounds), 450 / max(b[3] - b[1] for b in bounds))
        out = root / 'vpet/characters/dafeiyu-v1/animations' / name
        out.mkdir(parents=True, exist_ok=True)
        for i, (frame, box) in enumerate(zip(frames, bounds)):
            cropped = frame.crop(box)
            cropped = cropped.resize((round(cropped.width * scale), round(cropped.height * scale)), Image.Resampling.LANCZOS)
            canvas = Image.new('RGBA', (512, 512))
            canvas.alpha_composite(cropped, ((512 - cropped.width) // 2, 37 if name == 'reaction' else 487 - cropped.height))
            canvas.save(out / f'{i:03}.png')
        (out / 'clip.json').write_text(json.dumps(dict(durations=duration, frames=len(frames))), encoding='utf-8')
        result['animations'] += 1
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    print(json.dumps(prepare(parser.parse_args().root)))
