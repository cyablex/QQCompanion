"""Launch the official NapCat bootstrap for the configured QQ account."""

import os
import subprocess
import winreg
from pathlib import Path

from prepare_runtime import prepare_napcat
from settings import ROOT, read_settings

if __name__ == "__main__":
    if (ROOT / "config/offline-development.json").exists():
        print("Offline development: QQ login is disabled until manual resumption.")
        raise SystemExit(0)
    cfg = read_settings()
    if not cfg["bot_qq"]:
        raise SystemExit("Configure the bot QQ account first.")
    prepare_napcat()
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ",
    ) as key:
        uninstall = winreg.QueryValueEx(key, "UninstallString")[0].strip('"')
    qq = Path(uninstall).parent / "QQ.exe"
    if not qq.is_file():
        raise SystemExit("Registered QQ executable was not found.")
    base = ROOT / "napcat"
    os.environ["NAPCAT_QUICK_ACCOUNT"] = cfg["bot_qq"]
    for name, path in {
        "NAPCAT_PATCH_PACKAGE": base / "qqnt.json",
        "NAPCAT_LOAD_PATH": base / "loadNapCat.js",
        "NAPCAT_INJECT_PATH": base / "NapCatWinBootHook.dll",
        "NAPCAT_LAUNCHER_PATH": base / "NapCatWinBootMain.exe",
        "NAPCAT_MAIN_PATH": base / "napcat.mjs",
    }.items():
        os.environ[name] = str(path)
    os.chdir(base)
    raise SystemExit(
        subprocess.call(
            [
                str(base / "NapCatWinBootMain.exe"),
                str(qq),
                str(base / "NapCatWinBootHook.dll"),
                "-q",
                cfg["bot_qq"],
            ]
        )
    )
