"""Private offline resources, including the actual base Python used by singing."""

import json
import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from archive_crypto import decrypt_file, encrypt_file
from migration import product_version, safe_members


def venv_config(folder):
    path = Path(folder) / "pyvenv.cfg"
    if not path.is_file():
        raise ValueError("运行环境缺少 pyvenv.cfg。")
    return dict(
        line.split(" = ", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if " = " in line
    )


def repair_venv(folder, base):
    folder, base = Path(folder), Path(base)
    cfg = venv_config(folder)
    if not (base / "python.exe").is_file():
        raise ValueError("没有找到迁移后的基础 Python。")
    cfg.update(
        home=str(base),
        executable=str(base / "python.exe"),
        command=f'"{base / "python.exe"}" -m venv "{folder}"',
    )
    (folder / "pyvenv.cfg").write_text(
        "\n".join(f"{k} = {v}" for k, v in cfg.items()) + "\n", encoding="utf-8"
    )


def resource_files(root):
    root = Path(root).resolve()
    base311 = Path(venv_config(root.parent / "SeedVC/.venv")["home"])
    if not (base311 / "python.exe").is_file():
        raise ValueError("唱歌环境的基础 Python 不存在，不能生成完整资源包。")
    sources = [
        (root / "runtime", "main/runtime", set()),
        (root / "astrbot", "main/astrbot", {"data", ".git", ".venv", "logs"}),
        (root / "napcat", "main/napcat", {"config", "logs", "cache", "data"}),
        (root / "models", "main/models", set()),
        (root / "frontend/dist", "main/frontend/dist", set()),
        (root.parent / "GPTSoVITS/app", "voice/app", set()),
        (
            root.parent / "SeedVC",
            "singing",
            {"cache", "outputs", "examples", "data", ".git"},
        ),
        (
            root.parent / "QQCompanionFrontendTools/python-env",
            "desktop/python-env",
            set(),
        ),
        (base311, "python311", set()),
    ]
    for folder, prefix, excluded in sources:
        if not folder.is_dir():
            raise ValueError("运行资源缺失，不能生成完整离线资源包。")
        for directory, dirs, names in os.walk(folder):
            relative = Path(directory).relative_to(folder)
            dirs[:] = [
                d
                for d in sorted(dirs)
                if d not in {"__pycache__", ".pytest_cache", ".ruff_cache"}
                and not (relative == Path(".") and d in excluded)
            ]
            for name in sorted(names):
                path = Path(directory) / name
                if path.is_symlink():
                    raise ValueError("离线资源包含符号链接，需先确认实际文件。")
                if path.suffix in {".log", ".token", ".dpapi"} or name in {
                    ".condarc",
                    "pip.ini",
                    ".pypirc",
                }:
                    continue
                yield path, prefix + "/" + path.relative_to(folder).as_posix()
    yield root.parent / "GPTSoVITS/launch.py", "voice/launch.py"


def export_resources(root, output, password, progress=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("离线资源包已经存在，请使用新文件名。")
    output.parent.mkdir(parents=True, exist_ok=True)
    count = total = last_notice = 0
    with tempfile.TemporaryDirectory(
        prefix="dfy-resources-", dir=output.parent
    ) as scratch:
        zipped = Path(scratch) / "resources.zip"
        with zipfile.ZipFile(zipped, "x", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    dict(
                        format=1,
                        kind="offline-resources",
                        created=time.time(),
                        platform="windows-x64",
                        profiles=["chat", "desktop", "speech", "singing"],
                        credentials_included=False,
                        source_version=product_version(root),
                    )
                ),
            )
            for path, name in resource_files(root):
                archive.write(path, name)
                count += 1
                total += path.stat().st_size
                if progress and total - last_notice >= 1024**3:
                    progress(
                        dict(stage="pack", files=count, gib=round(total / 1024**3, 1))
                    )
                    last_notice = total
        if progress:
            progress(dict(stage="encrypt", files=count, gib=round(total / 1024**3, 1)))
        encrypt_file(zipped, output, password)
    return dict(files=count, bytes=output.stat().st_size, encrypted=True)


def restore_resources(backup, root, password, progress=None):
    root = Path(root).resolve()
    parent = root.parent
    targets = {
        "main/runtime": root / "runtime",
        "main/astrbot": root / "astrbot",
        "main/napcat": root / "napcat",
        "main/models": root / "models",
        "main/frontend/dist": root / "frontend/dist",
        "voice/app": parent / "GPTSoVITS/app",
        "voice/launch.py": parent / "GPTSoVITS/launch.py",
        "singing": parent / "SeedVC",
        "desktop": parent / "QQCompanionFrontendTools",
        "python311": parent / "QQCompanionRuntime311",
    }
    for target in targets.values():
        if target.exists():
            raise ValueError("目标已有运行资源，请选择尚未安装环境的新目录。")
    size = Path(backup).stat().st_size
    temp_volume = Path(tempfile.gettempdir()).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    shared_volume = temp_volume.anchor.casefold() == parent.anchor.casefold()
    if (
        shutil.disk_usage(temp_volume).free
        < size * (2 if shared_volume else 1) + 64 * 1024**2
    ):
        raise ValueError("临时磁盘空间不足，请腾出与离线资源包相当的空间后重试。")
    if not shared_volume and shutil.disk_usage(parent).free < size + 64 * 1024**2:
        raise ValueError("目标磁盘空间不足，请腾出与离线资源包相当的空间后重试。")
    installed = []
    # The decrypted intermediate uses the user temp volume; a full GPU resource
    # set need not occupy three copies of itself on the destination disk.
    with (
        tempfile.TemporaryDirectory(prefix="dfy-decrypt-") as plaintext,
        tempfile.TemporaryDirectory(prefix=".dfy-resources-", dir=parent) as staging,
    ):
        zipped = Path(plaintext) / "resources.zip"
        decrypt_file(backup, zipped, password)
        with zipfile.ZipFile(zipped) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest.get("format") != 1
                or manifest.get("kind") != "offline-resources"
                or manifest.get("platform") != "windows-x64"
            ):
                raise ValueError("不是受支持的 Windows 离线资源包。")
            entries = list(
                safe_members(
                    archive,
                    {
                        "manifest.json",
                        "main",
                        "voice",
                        "singing",
                        "desktop",
                        "python311",
                    },
                )
            )
            count = 0
            last_notice = time.monotonic()
            for entry in entries:
                if entry.filename != "manifest.json" and not any(
                    entry.filename == prefix or entry.filename.startswith(prefix + "/")
                    for prefix in targets
                ):
                    raise ValueError("资源包包含不允许的安装位置。")
                archive.extract(entry, staging)
                count += 1
                if progress and time.monotonic() - last_notice > 15:
                    progress(dict(stage="extract", files=count))
                    last_notice = time.monotonic()
        try:
            for prefix, target in targets.items():
                source = Path(staging) / prefix
                if not source.exists():
                    raise ValueError("离线资源包缺少必要组件。")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                installed.append(target)
            repair_venv(parent / "SeedVC/.venv", parent / "QQCompanionRuntime311")
            repair_venv(
                parent / "QQCompanionFrontendTools/python-env", root / "runtime"
            )
        except BaseException:
            for target in reversed(installed):
                target = target.resolve()
                if not target.is_relative_to(parent):
                    raise RuntimeError("资源回滚路径超出目标目录。")
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
            raise
    return dict(restored=True, profiles=manifest["profiles"])
