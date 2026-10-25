"""Start AstrBot with credentials available only in this process environment."""

import os
import runpy
import subprocess
import sys

from settings import ROOT, load_key


def configure_media_path():
    candidates = [ROOT / "runtime", ROOT / "tools/ffmpeg/bin"]
    candidates.extend(sorted((ROOT.parent / "GPTSoVITS/app").glob("*/runtime")))
    for directory in candidates:
        if (directory / "ffmpeg.exe").is_file():
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
            return str(directory)
    return None


if __name__ == "__main__":
    if (ROOT / "config/offline-development.json").exists():
        print("Offline development: online bot startup is disabled.")
        raise SystemExit(0)
    os.environ["QQCOMPANION_ROOT"] = str(ROOT)
    configure_media_path()
    os.environ["QQCOMPANION_DEEPSEEK_API_KEY"] = load_key()
    os.environ["QQCOMPANION_RELAY_API_KEY"] = load_key(relay=True)
    os.chdir(ROOT / "astrbot")
    sys.path.insert(0, str(ROOT / "astrbot"))
    # Importing AstrBot's defaults also initializes its global configuration.
    # Prepare in a separate process so this process only imports the final file.
    subprocess.run(
        [sys.executable, str(ROOT / "app/prepare_runtime.py"), "--framework-only"],
        check=True,
    )
    runpy.run_path(str(ROOT / "astrbot/main.py"), run_name="__main__")
