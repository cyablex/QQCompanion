"""Prepare the optional VPet Core host in this installation, without enabling it."""

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = '70263b91e3af9d68006b33c95ad63f59858ecbbe'
SDK_VERSION = '8.0.427'
ASSET_PREFIX = 'VPet-Simulator.Windows/mod/0000_core/pet/'
ASSET_GROUPS = ('vup/Default/Nomal/1/', 'vup/Touch_Head/A_Nomal/', 'vup/Touch_Head/B_Nomal/',
                'vup/Touch_Head/C_Nomal/', 'vup/Touch_Body/A_Happy/tb1/', 'vup/Touch_Body/B_Happy/tb1/',
                'vup/Touch_Body/C_Happy/tb1/', 'vup/Raise/Raised_Static/A_Nomal/',
                'vup/Raise/Raised_Static/B_Nomal/', 'vup/Raise/Raised_Static/C_Nomal/',
                'vup/Raise/Raised_Dynamic/Nomal/1/',
                'vup/MOVE/walk.left/A_Nomal/', 'vup/MOVE/walk.left/B_Nomal/', 'vup/MOVE/walk.left/C_Nomal/',
                'vup/MOVE/walk.right/A_Nomal/', 'vup/MOVE/walk.right/B_Nomal/', 'vup/MOVE/walk.right/C_Nomal/',
                'vup/Sleep/A_Nomal/', 'vup/Sleep/B_Nomal/', 'vup/Sleep/C_Nomal/')


def download(url, target):
    target = Path(target)
    if target.is_file() and target.stat().st_size:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + '.download')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'QQCompanion-VPet'}), timeout=60) as response, temp.open('wb') as out:
                shutil.copyfileobj(response, out)
            temp.replace(target)
            return
        except OSError:
            if attempt == 2:
                raise


def prepare(root=ROOT, sdk=None, video=False):
    root = Path(root).resolve()
    sdk = Path(sdk) if sdk else root / 'runtime/dotnet-sdk/dotnet.exe'
    if not sdk.is_file():
        archive = root / 'cache/vpet/dotnet-sdk.zip'
        download(f'https://dotnetcli.blob.core.windows.net/dotnet/Sdk/{SDK_VERSION}/dotnet-sdk-{SDK_VERSION}-win-x64.zip', archive)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(sdk.parent)
    tree_file = root / 'cache/vpet/upstream-tree.json'
    download(f'https://api.github.com/repos/LorisYounger/VPet/git/trees/{UPSTREAM}?recursive=1', tree_file)
    tree = json.loads(tree_file.read_text(encoding='utf-8'))['tree']
    selected = []
    for item in tree:
        path = item['path']
        if item['type'] != 'blob' or not path.startswith(ASSET_PREFIX):
            continue
        name = path[len(ASSET_PREFIX):]
        if name == 'vup.lps' or any(name.startswith(group) for group in ASSET_GROUPS):
            selected.append((path, root / 'vpet/assets' / name))

    def asset(item):
        path, target = item
        download(f'https://raw.githubusercontent.com/LorisYounger/VPet/{UPSTREAM}/' + urllib.parse.quote(path), target)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(asset, selected))
    download(f'https://raw.githubusercontent.com/LorisYounger/VPet/{UPSTREAM}/LICENSE', root / 'vpet/VPET-LICENSE.txt')
    (root / 'vpet/ASSET-SOURCE.txt').write_text(
        'VPet original demonstration animation assets by LB Game / VUP-Simulator.\n'
        'Source and animation terms: https://github.com/LorisYounger/VPet#动画版权声明与授权\n'
        f'Pinned commit: {UPSTREAM}\nAssets are not redistributed with QQCompanion source.\n', encoding='utf-8')
    env = {**os.environ, 'DOTNET_CLI_TELEMETRY_OPTOUT': '1', 'DOTNET_NOLOGO': '1'}
    from prepare_pet_character import prepare as prepare_character
    prepare_character(root)
    from prepare_pet_poses import prepare as prepare_poses
    from prepare_companion_art import prepare as prepare_companion_art
    prepare_poses(root)
    prepare_companion_art(root)
    if video:
        from prepare_pet_video import prepare as prepare_video
        prepare_video(root)
    subprocess.run([str(sdk), 'publish', str(root / 'integrations/vpet/DafeiyuPet.csproj'), '-c', 'Release',
                    '-r', 'win-x64', '--self-contained', 'false', '-o', str(root / 'vpet/host')], env=env, check=True)
    print(json.dumps(dict(prepared=True, assets=len(selected), enabled=False)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--sdk', type=Path)
    parser.add_argument('--video', action='store_true', help='同时准备可选MIT透明视频动作，需要FFmpeg')
    options = parser.parse_args()
    prepare(options.root, options.sdk, options.video)
