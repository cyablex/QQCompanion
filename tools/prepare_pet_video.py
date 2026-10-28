"""Prepare one consistent, MIT-licensed transparent animation pack; never enable the pet."""

import argparse
import json
import os
import shutil
import subprocess
import urllib.parse
from pathlib import Path

from prepare_vpet import download

ROOT = Path(__file__).resolve().parents[1]
REVISION = '48642698ee73ecf2029ea85a2e8f76f562b710e5'
UPSTREAM = 'https://raw.githubusercontent.com/PC2005-cloud/dsh-pet/' + REVISION
CLIPS = {
    'idle': '待机呼吸休闲', 'hum': '悠闲哼歌', 'look': '东张西望',
    'walk': '螃蟹走路', 'lift': '被鼠标拖拽悬空反馈',
    'head': '点击回应-挠痒咯咯笑', 'body': '点击回应-开心跃动',
    'wave': '点击回应-元气挥手', 'stretch': '超大伸懒腰',
    'write': '轻快记录', 'eat': '吃白饭', 'rest': '原地小憩沉眠',
    'yawn': '哈欠连天', 'greeting': '女仆屈膝礼仪',
}


def find_ffmpeg(root):
    for path in (root/'vpet/media/ffmpeg.exe', root/'tools/ffmpeg/bin/ffmpeg.exe'):
        if path.is_file():
            return path
    paths = sorted((root.parent/'GPTSoVITS/app').glob('*/runtime/ffmpeg.exe'))
    if paths:
        return paths[0]
    found = shutil.which('ffmpeg')
    if found:
        return Path(found)
    raise ValueError('需要支持 libvpx-vp9 透明解码的 FFmpeg；可用 --ffmpeg 指定。')


def prepare(root=ROOT, ffmpeg=None):
    root = Path(root).resolve()
    decoder = Path(ffmpeg).resolve() if ffmpeg else find_ffmpeg(root)
    if not decoder.is_file():
        raise ValueError('FFmpeg 文件不存在。')
    folder = root/'vpet/characters/dafeiyu-v1/video'
    folder.mkdir(parents=True, exist_ok=True)
    clips = {}
    for ident, name in CLIPS.items():
        source = root/'cache/pet-video'/ (name+'.webm')
        download(UPSTREAM+'/dsh-pet/assets/webm/'+urllib.parse.quote(name+'.webm'), source)
        target = folder/(ident+'.webm')
        if ident == 'lift':
            # Keep only the suspended middle section while the user holds her.
            subprocess.run([str(decoder), '-v', 'error', '-c:v', 'libvpx-vp9', '-ss', '1.5', '-i', str(source),
                            '-t', '4.5', '-an', '-c:v', 'libvpx-vp9', '-lossless', '1', '-pix_fmt', 'yuva420p',
                            '-auto-alt-ref', '0', '-y', str(target)], check=True)
        else:
            shutil.copyfile(source, target)
        poster = folder/(ident+'.png')
        # One square viewport and foot baseline for every action; alpha is kept.
        command = [str(decoder), '-v', 'error', '-c:v', 'libvpx-vp9', '-i', str(target),
                   '-vf', 'crop=360:360:140:0', '-frames:v', '1', '-y', str(poster)]
        subprocess.run(command, check=True)
        frames = 108 if ident == 'lift' else 241
        clips[ident] = dict(file=target.name, poster=poster.name, frames=frames, duration=frames / 24)
    download(UPSTREAM+'/LICENSE', folder/'LICENSE.txt')
    (folder/'SOURCE.txt').write_text('PC2005-cloud/dsh-pet, MIT licensed animation assets.\n'
        'https://github.com/PC2005-cloud/dsh-pet\nPinned commit: '+REVISION+'\n'
        'Only selected original clips are used; no model, agent or OCR code is installed.\n', encoding='utf-8')
    (folder/'pack.json').write_text(json.dumps(dict(width=360, height=360, fps=24, crop_x=140, clips=clips),
                                               ensure_ascii=False, indent=2), encoding='utf-8')
    executable = os.path.relpath(decoder, root) if decoder.is_relative_to(root.parent) else str(decoder)
    (folder/'decoder.json').write_text(json.dumps(dict(ffmpeg=executable), ensure_ascii=False), encoding='utf-8')
    return dict(clips=len(clips), bytes=sum(p.stat().st_size for p in folder.iterdir()), folder=str(folder))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--ffmpeg', type=Path)
    options = parser.parse_args()
    print(json.dumps(prepare(options.root, options.ffmpeg), ensure_ascii=False))
