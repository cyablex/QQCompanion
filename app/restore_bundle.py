"""Interactive USB restore: personal save plus private resources, always offline."""

import getpass
import json
import shutil
from pathlib import Path, PurePosixPath

from migration import restore_save
from resource_archive import restore_resources


def install_public_source(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    manifest = json.loads((source / "PUBLIC_SOURCE.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("恢复工具夹缺少受支持的公开源码清单。")
    copies = []
    for name in manifest["files"]:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or any(":" in p for p in path.parts)
        ):
            raise ValueError("源码清单包含不允许的路径。")
        if (
            path.parts[0]
            not in {"app", "tests", "tools", "plugins", "frontend", "docs", "integrations", ".github"}
            and len(path.parts) != 1
        ):
            raise ValueError("源码清单包含非源码目录。")
        if path.parts[0] in {"config", "data", "assets", "runtime", ".git"}:
            raise ValueError("源码清单不能安装私有数据或提交历史。")
        original, final = source / name, target / name
        if not original.is_file() or original.is_symlink():
            raise ValueError("恢复工具夹的源码文件缺失。")
        if final.exists() and final.read_bytes() != original.read_bytes():
            raise ValueError("目标已有不同版本源码，请使用新的恢复目录。")
        copies.append((original, final))
    for original, final in copies:
        final.parent.mkdir(parents=True, exist_ok=True)
        if not final.exists():
            shutil.copy2(original, final)
    shutil.copy2(source / "PUBLIC_SOURCE.json", target / "PUBLIC_SOURCE.json")


def main():
    save = Path(input("私人存档 .dfysave 完整路径：").strip().strip('"'))
    resources = Path(input("离线资源 .dfyresources 完整路径：").strip().strip('"'))
    target = Path(input("新电脑的大肥鱼目录（全新目录）：").strip().strip('"'))
    password = getpass.getpass("恢复口令（不会显示）：")
    restore_save(save, target, password)
    install_public_source(Path(__file__).resolve().parents[1], target)
    restore_resources(
        resources, target, password, progress=lambda data: print(data, flush=True)
    )
    print(
        "私人存档与环境已恢复，当前保持离线。请按迁移说明核对环境、重新填写Key和登录QQ。"
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileExistsError) as exc:
        raise SystemExit(str(exc)) from None
