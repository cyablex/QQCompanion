"""Build a separate 60-fps, faster animation pack for review/deployment."""

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


def frame_plan(source_frames, source_fps, fps, speed):
    if source_frames < 2 or source_fps <= 0 or fps not in (48, 60) or not .8 <= speed <= 2:
        raise ValueError('无效的帧率或速度。')
    return round((source_frames + 1) * fps / (source_fps * speed)), math.ceil(source_frames * fps / (source_fps * speed))


def restore_alpha(black, white):
    black, white = np.asarray(black, dtype=np.float32), np.asarray(white, dtype=np.float32)
    alpha = np.clip(255 - np.median(white-black, axis=2), 0, 255)
    alpha[alpha < 3] = 0
    alpha[alpha > 252] = 255
    rgb = np.clip(black*255/np.maximum(alpha[:, :, None], 1), 0, 255)
    rgb[alpha == 0] = 0
    return Image.fromarray(np.concatenate([rgb, alpha[:, :, None]], axis=2).round().astype(np.uint8), 'RGBA')


def prepare(source, output, ffmpeg, rife, model, fps=60, speed=1.35):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('输出目录必须是新目录，避免覆盖当前动作包。')
    pack = json.loads((source/'pack.json').read_text(encoding='utf-8'))
    if pack['width'] != 360 or pack['height'] != 360 or pack['crop_x'] != 140:
        raise ValueError('只支持已准备的蓝发女仆动作包。')
    output.mkdir(parents=True)
    cache = output.parent/'smooth-frames'
    result = dict(pack, fps=fps, speed=speed, clips={})
    for name, entry in pack['clips'].items():
        if Path(name).name != name or Path(entry['file']).name != entry['file']:
            raise ValueError('无效的动作文件名。')
        frames = entry['frames']
        count, kept = frame_plan(frames, pack['fps'], fps, speed)
        folder = cache/name
        for part in ['original', 'black-in', 'white-in', 'black-out', 'white-out', 'rgba']:
            (folder/part).mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen([str(ffmpeg), '-v', 'error', '-c:v', 'libvpx-vp9', '-i', str(source/entry['file']),
            '-vf', 'crop=360:360:140:0', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'], stdout=subprocess.PIPE)
        index = 0
        first = None
        try:
            while data := process.stdout.read(360*360*4):
                if len(data) != 360*360*4:
                    raise ValueError('原片帧数据不完整。')
                frame = Image.frombytes('RGBA', (360, 360), data)
                frame.save(folder/'original'/f'{index:08d}.png', compress_level=2)
                for key, colour in [('black', (0, 0, 0, 255)), ('white', (255, 255, 255, 255))]:
                    Image.alpha_composite(Image.new('RGBA', frame.size, colour), frame).convert('RGB').save(folder/(key+'-in')/f'{index:08d}.png', compress_level=2)
                if first is None:
                    first = frame.copy()
                index += 1
            if process.wait() != 0 or index != frames:
                raise ValueError('原片帧数与目录不符。')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        for key, colour in [('black', (0, 0, 0, 255)), ('white', (255, 255, 255, 255))]:
            Image.alpha_composite(Image.new('RGBA', first.size, colour), first).convert('RGB').save(folder/(key+'-in')/f'{frames:08d}.png', compress_level=2)
            with (folder/(key+'.log')).open('w') as log:
                subprocess.run([str(rife), '-i', str(folder/(key+'-in')), '-o', str(folder/(key+'-out')),
                    '-n', str(count), '-m', str(model), '-j', '1:1:1'], stdout=log, stderr=log, check=True)
        for i in range(kept):
            with Image.open(folder/'black-out'/f'{i+1:08d}.png') as black, Image.open(folder/'white-out'/f'{i+1:08d}.png') as white:
                image = restore_alpha(black, white)
            image.save(folder/'rgba'/f'{i:08d}.png', compress_level=2)
        movie = output/(name+'.webm')
        subprocess.run([str(ffmpeg), '-v', 'error', '-framerate', str(fps), '-i', str(folder/'rgba/%08d.png'),
            '-vf', 'pad=640:360:140:0:color=black@0', '-an', '-c:v', 'libvpx-vp9', '-lossless', '1',
            '-pix_fmt', 'yuva420p', '-auto-alt-ref', '0', '-cpu-used', '4', '-threads', '2', '-y', str(movie)], check=True)
        shutil.copyfile(source/entry['poster'], output/entry['poster'])
        result['clips'][name] = dict(entry, file=movie.name, frames=kept, duration=kept/fps)
        print(json.dumps(dict(clip=name, fps=fps, speed=speed, frames=kept, bytes=movie.stat().st_size)), flush=True)
    for filename in ['decoder.json', 'LICENSE.txt', 'SOURCE.txt']:
        shutil.copyfile(source/filename, output/filename)
    shutil.copyfile(Path(__file__).resolve().parents[1]/'integrations/vpet/licenses/RIFE-MIT.txt', output/'RIFE-MIT.txt')
    with (output/'SOURCE.txt').open('a', encoding='utf-8') as log:
        log.write(f'\nDerivative smoothing: RIFE ncnn Vulkan v4.6, dual-matte alpha reconstruction; {fps}fps, approximately {speed}x. Not original character animation.\nhttps://github.com/nihui/rife-ncnn-vulkan (MIT)\n')
    (output/'pack.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('--rife', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--speed', type=float, default=1.35)
    args = parser.parse_args()
    prepare(args.source, args.output, args.ffmpeg, args.rife, args.model, args.fps, args.speed)
