"""Build a separate 60-fps, faster animation pack for review/deployment."""

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image


def output_frame_count(source_frames, source_fps, fps, speed):
    if source_frames < 2 or source_fps <= 0 or fps not in (48, 60) or not .8 <= speed <= 2:
        raise ValueError('无效的帧率或速度。')
    return math.ceil(source_frames * fps / (source_fps * speed))


def restore_alpha(black, white):
    black, white = np.asarray(black, dtype=np.float32), np.asarray(white, dtype=np.float32)
    alpha = np.clip(255 - np.median(white-black, axis=2), 0, 255)
    alpha[alpha < 3] = 0
    alpha[alpha > 252] = 255
    rgb = np.clip(black*255/np.maximum(alpha[:, :, None], 1), 0, 255)
    rgb[alpha == 0] = 0
    return Image.fromarray(np.concatenate([rgb, alpha[:, :, None]], axis=2).round().astype(np.uint8), 'RGBA')


def cadence_samples(frames):
    """Centre isolated duplicates from a detected three-frame source cadence.

    Keep the original time axis, including intentional rests. Only the periodic
    single duplicate is removed; interpolation spans its midpoint instead of
    repeatedly easing to a complete stop.
    """
    if len(frames) < 24:
        return list(range(len(frames))), np.arange(len(frames), dtype=float)
    pixels = np.stack([np.asarray(f.resize((128, 128)), dtype=np.float32) for f in frames])
    pixels[:, :, :, :3] *= pixels[:, :, :, 3:] / 255
    difference = np.abs(np.diff(pixels, axis=0)).mean(axis=(1, 2, 3))
    still = difference < .05
    isolated = still & ~np.r_[False, still[:-1]] & ~np.r_[still[1:], False]
    candidates = np.flatnonzero(isolated)
    phases = np.bincount(candidates % 3, minlength=3)
    phase = int(phases.argmax())
    if len(candidates) < len(frames) * .15 or phases[phase] < len(candidates) * .85:
        return list(range(len(frames))), np.arange(len(frames), dtype=float)
    duplicates = set(int(i) for i in candidates if i % 3 == phase)
    keep = [i for i in range(len(frames)) if i - 1 not in duplicates]
    return keep, np.array([i + .5 if i in duplicates else i for i in keep])


def sample_indices(positions, count, source_fps, fps, speed, subdivisions=4):
    times = np.arange(count) * speed * source_fps / fps
    return np.interp(times, positions, np.arange(len(positions)) * subdivisions).round().astype(int)


def interpolate(frames, folder, rife, model, count):
    for key, colour in [('black', (0, 0, 0, 255)), ('white', (255, 255, 255, 255))]:
        source, target = folder/(key+'-in'), folder/(key+'-out')
        source.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(frames):
            Image.alpha_composite(Image.new('RGBA', frame.size, colour), frame).convert('RGB').save(source/f'{i:08d}.png', compress_level=2)
        with (folder/(key+'.log')).open('w') as log:
            subprocess.run([str(rife), '-i', str(source), '-o', str(target), '-n', str(count),
                '-m', str(model), '-j', '1:1:1'], stdout=log, stderr=log, check=True)


def restored(folder, index):
    with Image.open(folder/'black-out'/f'{index+1:08d}.png') as black, Image.open(folder/'white-out'/f'{index+1:08d}.png') as white:
        return restore_alpha(black, white)


def prepare_clip(source, entry, folder, movie, ffmpeg, rife, model, source_fps, fps, speed):
    kept = output_frame_count(entry['frames'], source_fps, fps, speed)
    process = subprocess.Popen([str(ffmpeg), '-v', 'error', '-c:v', 'libvpx-vp9', '-i', str(source/entry['file']),
        '-vf', 'crop=360:360:140:0', '-vsync', '0', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'], stdout=subprocess.PIPE)
    frames = []
    try:
        while data := process.stdout.read(360*360*4):
            if len(data) != 360*360*4:
                raise ValueError('原片帧数据不完整。')
            frames.append(Image.frombytes('RGBA', (360, 360), data))
        if process.wait() != 0 or len(frames) != entry['frames']:
            raise ValueError('原片帧数与目录不符。')
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    keep, positions = cadence_samples(frames)
    core = folder/'core'
    interpolate([frames[i] for i in keep], core, rife, model, len(keep)*4)
    indices = sample_indices(positions, kept, source_fps, fps, speed)
    bridge = min(round(.3*fps), kept-1)
    rgba = folder/'rgba'
    rgba.mkdir(parents=True)
    first = None
    last = None
    for i in range(kept-bridge):
        image = restored(core, indices[i])
        image.save(rgba/f'{i:08d}.png', compress_level=2)
        if first is None:
            first = image.copy()
        last = image
    # Explicitly allocate 0.3 seconds to returning to the starting pose. A
    # single appended endpoint can turn a large pose change into a one-frame snap.
    loop = folder/'loop'
    interpolate([last, first], loop, rife, model, 2*(bridge+1))
    for i in range(bridge):
        restored(loop, i+1).save(rgba/f'{kept-bridge+i:08d}.png', compress_level=2)
    subprocess.run([str(ffmpeg), '-v', 'error', '-framerate', str(fps), '-i', str(rgba/'%08d.png'),
        '-vf', 'pad=640:360:140:0:color=black@0', '-an', '-c:v', 'libvpx-vp9', '-lossless', '1',
        '-pix_fmt', 'yuva420p', '-auto-alt-ref', '0', '-lag-in-frames', '0', '-cpu-used', '4', '-threads', '2', '-y', str(movie)], check=True)
    # Older libvpx/FFmpeg builds can lose tail frames with encoder lookahead.
    # Read back the actual stream before declaring its timing in the manifest.
    decoded = subprocess.check_output([str(ffmpeg), '-v', 'error', '-c:v', 'libvpx-vp9', '-i', str(movie),
        '-vf', 'scale=1:1', '-vsync', '0', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'])
    if len(decoded) != kept*4:
        raise ValueError(f'成片实际帧数不符：预期{kept}，读回{len(decoded)//4}。')
    return kept, len(frames)-len(keep)


def prepare(source, output, ffmpeg, rife, model, fps=60, speed=1.35):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('输出目录必须是新目录，避免覆盖当前动作包。')
    pack = json.loads((source/'pack.json').read_text(encoding='utf-8'))
    if pack['width'] != 360 or pack['height'] != 360 or pack['crop_x'] != 140:
        raise ValueError('只支持已准备的蓝发女仆动作包。')
    output.mkdir(parents=True)
    cache = output.with_name(output.name+'-frames')
    result = dict(pack, fps=fps, speed=speed, clips={})
    for name, entry in pack['clips'].items():
        if Path(name).name != name or Path(entry['file']).name != entry['file']:
            raise ValueError('无效的动作文件名。')
        folder = cache/name
        movie = output/(name+'.webm')
        kept, removed = prepare_clip(source, entry, folder, movie, ffmpeg, rife, model, pack['fps'], fps, speed)
        shutil.copyfile(source/entry['poster'], output/entry['poster'])
        result['clips'][name] = dict(entry, file=movie.name, frames=kept, duration=kept/fps)
        print(json.dumps(dict(clip=name, fps=fps, speed=speed, frames=kept, cadence_duplicates=removed, bytes=movie.stat().st_size)), flush=True)
    for filename in ['decoder.json', 'LICENSE.txt', 'SOURCE.txt']:
        shutil.copyfile(source/filename, output/filename)
    shutil.copyfile(Path(__file__).resolve().parents[1]/'integrations/vpet/licenses/RIFE-MIT.txt', output/'RIFE-MIT.txt')
    with (output/'SOURCE.txt').open('a', encoding='utf-8') as log:
        log.write(f'\nDerivative smoothing: RIFE ncnn Vulkan v4.6, dual-matte alpha reconstruction; {fps}fps, approximately {speed}x. Source-cadence correction on the original time axis; 0.3s loop return. Not original character animation.\nhttps://github.com/nihui/rife-ncnn-vulkan (MIT)\n')
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
