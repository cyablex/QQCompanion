"""Prepare a fresh Windows installation from pinned dependencies, never the live one."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ASTRBOT_COMMIT = "dffe922773725e9c178392c423676ad65044748c"


def unpack_zip(source, target, strip_first=False):
    target = Path(target)
    if target.exists():
        raise ValueError("组件目录已经存在，不会覆盖。")
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        for entry in entries:
            name = PurePosixPath(entry.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in entry.orig_filename
                or any(":" in part for part in name.parts)
            ):
                raise ValueError("组件压缩包包含不允许的路径。")
        with tempfile.TemporaryDirectory(
            prefix=".dfy-component-", dir=target.parent
        ) as scratch:
            archive.extractall(scratch)
            source_root = Path(scratch)
            if strip_first:
                children = list(source_root.iterdir())
                if len(children) != 1 or not children[0].is_dir():
                    raise ValueError("组件压缩包目录结构不匹配。")
                source_root = children[0]
            shutil.move(str(source_root), str(target))


def run(args, cwd=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def copy_python_base(base, target):
    """Create the standalone runtime/python.exe layout used by this product."""
    base, target = Path(base), Path(target)
    if target.exists():
        raise ValueError("存在未完成的 runtime 目录，请检查后换一个干净目录准备。")
    target.mkdir(parents=True)
    for path in base.iterdir():
        if path.is_file() and (
            path.suffix.lower() == ".dll"
            or path.name.lower() in {"python.exe", "pythonw.exe", "license.txt"}
        ):
            shutil.copy2(path, target / path.name)
    for name in ("Lib", "DLLs", "tcl", "Library"):
        if (base / name).is_dir():
            shutil.copytree(
                base / name,
                target / name,
                ignore=shutil.ignore_patterns("site-packages", "__pycache__"),
            )
    if not (target / "python.exe").is_file():
        raise ValueError("基础 Python 缺少 Windows 解释器。")


def prepare(root, napcat_zip=None, skip_frontend=False):
    root = Path(root).resolve()
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        raise ValueError(
            "准备脚本需要 Windows x64 与 Python 3.12，请先安装对应 Python。"
        )
    cfg_path = root / "config/user.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        if cfg.get("live") or not (root / "config/offline-development.json").exists():
            raise ValueError("这是已有个人安装；准备脚本不会重装或覆盖它。")
    for folder in ("config", "data", "logs", "cache", "backups", "models", "assets"):
        (root / folder).mkdir(exist_ok=True)
    if not cfg_path.exists():
        shutil.copy2(root / "config.example.json", cfg_path)
    marker = root / "config/offline-development.json"
    if not marker.exists():
        marker.write_text(json.dumps(dict(reason="new-install")), encoding="utf-8")
    runtime = root / "runtime/python.exe"
    if not runtime.exists():
        copy_python_base(sys.base_prefix, root / "runtime")
        run([runtime, "-m", "ensurepip"])
    run([runtime, "-m", "pip", "install", "-r", root / "requirements-runtime.lock"])
    run([runtime, "-m", "pip", "check"])
    if not (root / "astrbot").exists():
        with tempfile.TemporaryDirectory(prefix="dfy-download-") as scratch:
            downloaded = Path(scratch) / "astrbot.zip"
            request = urllib.request.Request(
                f"https://codeload.github.com/AstrBotDevs/AstrBot/zip/{ASTRBOT_COMMIT}",
                headers={"User-Agent": "QQCompanion-setup"},
            )
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                downloaded.open("xb") as output,
            ):
                shutil.copyfileobj(response, output)
            unpack_zip(downloaded, root / "astrbot", strip_first=True)
    if not (root / "astrbot/dashboard-release/dist").is_dir():
        with tempfile.TemporaryDirectory(prefix="dfy-dashboard-") as scratch:
            downloaded = Path(scratch) / "dashboard.zip"
            request = urllib.request.Request(
                "https://github.com/AstrBotDevs/AstrBot/releases/download/v4.29.1/AstrBot-v4.29.1-dashboard.zip",
                headers={"User-Agent": "QQCompanion-setup"},
            )
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                downloaded.open("xb") as output,
            ):
                shutil.copyfileobj(response, output)
            unpack_zip(downloaded, root / "astrbot/dashboard-release")
    desktop = root.parent / "QQCompanionFrontendTools/python-env"
    if not (desktop / "Scripts/python.exe").exists():
        run([runtime, "-m", "venv", desktop])
    run(
        [
            desktop / "Scripts/python.exe",
            "-m",
            "pip",
            "install",
            "-r",
            root / "requirements-desktop.lock",
        ]
    )
    if napcat_zip:
        unpack_zip(napcat_zip, root / "napcat")
    if not skip_frontend:
        npm = shutil.which("npm.cmd")
        if not npm:
            raise ValueError(
                "请安装 Node.js（满足 frontend/package.json 的引擎要求），然后重跑准备脚本。"
            )
        run([npm, "ci", "--prefix", root / "frontend"])
        run([npm, "run", "build", "--prefix", root / "frontend"])
    print(
        "环境已准备，当前保持离线。请安装官方 QQ、准备 NapCat、填写账号和 Key，再显式启用。"
    )


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="准备新的大肥鱼安装，不启动 QQ、不发送消息。"
    )
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--napcat-zip", type=Path)
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.root, args.napcat_zip, args.skip_frontend)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
