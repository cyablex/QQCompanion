"""Launch the official portable bundle with local caches on D:."""

import os
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app" / "GPT-SoVITS-v2pro-20261006nvidia50x0"
PYTHON = APP / "runtime" / "python.exe"


def configure_environment():
    folders = {
        "TEMP": ROOT / "temp",
        "TMP": ROOT / "temp",
        "TMPDIR": ROOT / "temp",
        "XDG_CACHE_HOME": ROOT / "cache",
        "HF_HOME": ROOT / "cache" / "huggingface",
        "HF_HUB_CACHE": ROOT / "cache" / "huggingface" / "hub",
        "HUGGINGFACE_HUB_CACHE": ROOT / "cache" / "huggingface" / "hub",
        "TORCH_HOME": ROOT / "cache" / "torch",
        "MODELSCOPE_CACHE": ROOT / "cache" / "modelscope",
        "GRADIO_TEMP_DIR": ROOT / "temp" / "gradio",
        "NUMBA_CACHE_DIR": ROOT / "cache" / "numba",
        "MPLCONFIGDIR": ROOT / "cache" / "matplotlib",
        "CUDA_CACHE_PATH": ROOT / "cache" / "cuda",
        "TRITON_CACHE_DIR": ROOT / "cache" / "triton",
        "TORCHINDUCTOR_CACHE_DIR": ROOT / "cache" / "torchinductor",
        "PIP_CACHE_DIR": ROOT / "cache" / "pip",
    }
    for name, folder in folders.items():
        folder.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(folder)
    os.environ.update(
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
        GRADIO_ANALYTICS_ENABLED="False",
        GRADIO_SERVER_NAME="127.0.0.1",
        is_share="False",
        language="zh_CN",
        version="v5turbo",
        gpt_path="GPT_SoVITS/pretrained_models/s1v3.ckpt",
        sovits_path="GPT_SoVITS/pretrained_models/gsv-v5-pretrained/s2Gv5turbo.pth",
    )
    os.environ["PATH"] = os.pathsep.join(
        [str(APP / "runtime"), str(APP / "runtime" / "Scripts"), str(APP), os.environ["PATH"]]
    )


def main():
    companion_settings = Path(os.environ.get("QQCOMPANION_ROOT", ROOT.parent / "QQCompanion")) / "config/user.json"

    def game_mode():
        return companion_settings.exists() and json.loads(companion_settings.read_text(encoding="utf-8")).get("game_mode", False)

    if game_mode():
        raise SystemExit("Game mode is on. Disable it before starting local voice inference.")
    configure_environment()
    mode = sys.argv[1] if len(sys.argv) > 1 else "webui"
    if mode == "webui":
        args = [str(PYTHON), "-I", "-X", "utf8", "-u", str(APP / "GPT_SoVITS" / "inference_webui.py"), "zh_CN"]
    elif mode == "api":
        args = [str(PYTHON), "-I", "-X", "utf8", "-u", str(APP / "api_v2.py"), "-a", "127.0.0.1", "-p", "9880", "-c", str(ROOT / "tts_local.yaml")]
    else:
        raise SystemExit("Usage: launch.py [webui|api]")
    process = subprocess.Popen(args, cwd=APP, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        while True:
            try:
                raise SystemExit(process.wait(timeout=2))
            except subprocess.TimeoutExpired:
                if game_mode():
                    break
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
