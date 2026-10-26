"""Non-sending installation diagnostics; no credentials or chat content in output."""

import argparse
import json
import os
import subprocess
from pathlib import Path


def probe(executable, statement, root):
    executable, root = Path(executable), Path(root)
    if not executable.is_file():
        return dict(ok=False, reason="missing-python")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONHOME", "PYTHONPATH", "CONDA_PREFIX", "VIRTUAL_ENV"}
    }
    env.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1", QQCOMPANION_ROOT=str(root))
    windows = Path(os.environ.get("SystemRoot", "C:/Windows"))
    env["PATH"] = os.pathsep.join(
        [str(executable.parent), str(windows / "System32"), str(windows)]
    )
    try:
        result = subprocess.run(
            [str(executable), "-X", "utf8", "-c", statement],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
        )
        if result.returncode:
            return dict(ok=False, reason="import-or-runtime-error")
        return dict(ok=True, detail=json.loads(result.stdout.splitlines()[-1]))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return dict(ok=False, reason="probe-failed")


def check(root, deep=False):
    root = Path(root).resolve()
    result = dict(
        offline=(root / "config/offline-development.json").is_file(),
        configured=(root / "config/user.json").is_file(),
        frontend=(root / "frontend/dist/index.html").is_file(),
        astrbot=(root / "astrbot/main.py").is_file(),
        napcat=all(
            (root / "napcat" / name).is_file()
            for name in ("napcat.mjs", "NapCatWinBootMain.exe", "NapCatWinBootHook.dll")
        ),
        voice_models=(root.parent / "GPTSoVITS/app").is_dir(),
        reference=(root.parent / "GPTSoVITS/reference/yui/presets.json").is_file(),
    )
    if deep:
        result["core"] = probe(
            root / "runtime/python.exe",
            "import json,sys,psutil,PIL,cryptography; print(json.dumps({'python':sys.version.split()[0], 'prefix_local':sys.prefix==sys.argv[1]}))".replace(
                "sys.argv[1]", repr(str(root / "runtime"))
            ),
            root,
        )
        result["desktop"] = probe(
            root.parent / "QQCompanionFrontendTools/python-env/Scripts/python.exe",
            "import json,sys,webview;sys.path.insert(0,"
            + repr(str(root / 'app'))
            + ");from desktop_api import DesktopAPI;print(json.dumps({'api_import':True,'base_local':sys.base_prefix=="
            + repr(str(root / "runtime"))
            + "}))",
            root,
        )
        result["singing"] = probe(
            root.parent / "SeedVC/.venv/Scripts/python.exe",
            "import json,sys,torch,demucs,soundfile;print(json.dumps({'cuda':torch.cuda.is_available(),'torch':torch.__version__,'base_local':sys.base_prefix=="
            + repr(str(root.parent / "QQCompanionRuntime311"))
            + "}))",
            root,
        )
        speech = sorted((root.parent / "GPTSoVITS/app").glob("*/runtime/python.exe"))
        result["speech"] = probe(
            speech[0] if speech else root.parent / "GPTSoVITS/missing-python.exe",
            "import json,sys,torch;print(json.dumps({'cuda':torch.cuda.is_available(),'prefix_local':sys.prefix=="
            + repr(str(speech[0].parent) if speech else "")
            + "}))",
            root,
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="只检查环境，不登录或发送消息。")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--deep", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check(args.root, args.deep), ensure_ascii=False, indent=2))
