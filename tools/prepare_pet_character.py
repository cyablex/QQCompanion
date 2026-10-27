"""Transcode the owner's existing transparent emotes into a local VPet character.

No character images are downloaded, generated or exported with public source.
"""

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
CHARACTER = 'dafeiyu-v1'
EMOTES = {'idle': 'maid_question', 'head': 'maid_pat', 'body': 'maid_angry',
          'move': 'maid_happy', 'sleep': 'maid_sleep', 'raised': 'maid_question'}


def dominant_bounds(frame):
    width, height = frame.size
    remaining = bytearray(1 if a >= 32 else 0 for a in frame.getchannel('A').tobytes())
    best, bounds = 0, None
    for start in range(len(remaining)):
        if not remaining[start]:
            continue
        remaining[start] = 0
        stack, count = [start], 0
        left = right = start % width
        top = bottom = start // width
        while stack:
            index = stack.pop()
            count += 1
            x, y = index % width, index // width
            left, right, top, bottom = min(left, x), max(right, x), min(top, y), max(bottom, y)
            for neighbor in ([index - 1] if x else []) + ([index + 1] if x + 1 < width else []) + ([index - width] if y else []) + ([index + width] if y + 1 < height else []):
                if remaining[neighbor]:
                    remaining[neighbor] = 0
                    stack.append(neighbor)
        if count > best:
            best, bounds = count, (left, top, right + 1, bottom + 1)
    return bounds


def prepare(root=ROOT, rebuild=False):
    root = Path(root).resolve()
    catalog = root / 'assets/memes/catalog.json'
    if not catalog.is_file():
        return None
    items = {item['id']: Path(item['file']).resolve() for item in json.loads(catalog.read_text(encoding='utf-8'))}
    if any(name not in items or not items[name].is_file()
           or not items[name].is_relative_to(root / 'assets/memes') for name in EMOTES.values()):
        return None
    pack = root / 'vpet/characters' / CHARACTER
    if rebuild and pack.exists():
        if pack.resolve().parent != (root / 'vpet/characters').resolve() or pack.name != CHARACTER:
            raise ValueError('Generated character path is outside this installation')
        shutil.rmtree(pack)
    if (pack / 'character.json').is_file():
        return pack
    pack.mkdir(parents=True, exist_ok=True)
    graph_lines = []
    for action, ident in EMOTES.items():
        with Image.open(items[ident]) as animation:
            frames, durations = [], []
            for frame in ImageSequence.Iterator(animation):
                rgba = frame.convert('RGBA')
                frames.append(rgba)
                durations.append(max(40, frame.info.get('duration', 80)))
        # The largest alpha component is the character. This excludes the old
        # video frame border; use a union crop for stable animation coordinates.
        bounds = [box for frame in frames if (box := dominant_bounds(frame))]
        if not bounds:
            raise ValueError(f'Empty animation: {ident}')
        crop = (min(b[0] for b in bounds), min(b[1] for b in bounds),
                max(b[2] for b in bounds), max(b[3] for b in bounds))
        scale = min(430 / (crop[2] - crop[0]), 440 / (crop[3] - crop[1]))
        size = (round((crop[2] - crop[0]) * scale), round((crop[3] - crop[1]) * scale))
        rendered = []
        for frame in frames:
            canvas = Image.new('RGBA', (500, 500))
            content = frame.crop(crop).resize(size, Image.Resampling.LANCZOS)
            canvas.alpha_composite(content, ((500 - size[0]) // 2, 480 - size[1]))
            rendered.append(canvas)
        portions = {'Single': list(range(len(frames)))} if action == 'idle' else {
            'A_Start': list(range(min(3, len(frames)))),
            'B_Loop': list(range(len(frames))),
            'C_End': list(range(max(0, len(frames) - 3), len(frames)))}
        graph = {'idle': 'Default', 'head': 'Touch_Head', 'body': 'Touch_Body',
                 'sleep': 'Sleep', 'raised': 'Raised_Static', 'move': 'Move'}[action]
        for phase, indices in portions.items():
            folder = pack / 'frames' / action / phase
            folder.mkdir(parents=True, exist_ok=True)
            for index in indices:
                rendered[index].save(folder / f'{index:04d}_{durations[index]}.png')
            names = ['walk.left', 'walk.right'] if action == 'move' else [action]
            for name in names:
                graph_lines.append(f'pnganimation#{name}:|path#{action}/{phase}:|graph#{graph}:|animat#{phase}:|mode#Nomal:|')
                if action == 'raised' and phase == 'B_Loop':
                    graph_lines.append(f'pnganimation#raised.dynamic:|path#{action}/{phase}:|graph#Raised_Dynamic:|animat#Single:|mode#Nomal:|')
    (pack / 'frames/info.lps').write_text('\n'.join(graph_lines), encoding='utf-8')
    (pack / 'pet.lps').write_text(
        'pet#dafeiyu:|intor#本机女仆表情动作:|path#frames:|petname#大肥鱼:|\n'
        'touchhead:|px#130:|py#60:|sw#240:|sh#210:|\n'
        'touchbody:|px#140:|py#275:|sw#230:|sh#170:|\n'
        'touchraised:|nomal_px#80:|nomal_py#40:|nomal_sw#340:|nomal_sh#220:|\n'
        'raisepoint:|nomal_x#250:|nomal_y#140:|\n'
        'move:|graph#walk.left:|TriggerLeft#160:|TriggerType#16:|CheckLeft#80:|CheckType#16:|SpeedX#-8:|Distance#5:|ModeType#12:|\n'
        'move:|graph#walk.right:|TriggerRight#160:|TriggerType#32:|CheckRight#80:|CheckType#32:|SpeedX#8:|Distance#5:|ModeType#12:|\n'
        'duration:|state#10:|squat#20:|boring#20:|sleep#20:|\n'
        'side:|left#200:|right#300:|\n', encoding='utf-8')
    (pack / 'character.json').write_text(json.dumps(dict(name='大肥鱼·蓝发女仆',
        kind='existing_local_emotes', actions=list(EMOTES), source_ids=sorted(set(EMOTES.values())),
        note='基础表情动作；移动复用开心轻跳，尚非完整逐帧行走或口型动作集。'), ensure_ascii=False, indent=2), encoding='utf-8')
    return pack


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--rebuild', action='store_true')
    args = parser.parse_args()
    print(json.dumps(dict(character=str(prepare(args.root, args.rebuild) or ''), enabled_unchanged=True)))
