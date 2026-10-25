"""Local Seed-VC singing conversion entry; reference voice alone is not a song."""

import argparse
import json
import os
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="把实际唱出的干声转换为大肥鱼的日常参考音色")
    parser.add_argument("--source", type=Path, required=True, help="已有旋律和歌词的清唱/歌曲干声，不能用朗读代替")
    parser.add_argument("--target", type=Path, default=ROOT.parent / "GPTSoVITS/reference/yui/everyday.wav")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs")
    parser.add_argument("--steps", type=int, choices=range(10, 61), default=35)
    parser.add_argument("--shift", type=int, choices=range(-12, 13), default=0)
    args = parser.parse_args()
    if not args.source.is_file() or not args.target.is_file():
        parser.error("需要实际存在的歌声干声与参考音色文件")
    config = Path(os.environ.get("QQCOMPANION_ROOT", ROOT.parent / "QQCompanion")) / "config/user.json"
    if config.exists() and json.loads(config.read_text(encoding="utf-8")).get("game_mode"):
        parser.error("游戏模式已开启，请先关闭游戏模式")
    os.chdir(ROOT)
    for key, value in urllib.request.getproxies().items():
        if key in {"http", "https"}:
            os.environ.setdefault(key.upper() + "_PROXY", value)
    os.environ.setdefault("HF_HOME", str(ROOT / "checkpoints/hf_home"))
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    # The installed singing models are fixed and already cached. Song requests
    # must not wait for online metadata checks before using those local files.
    # Maintainers can explicitly set HF_HUB_OFFLINE=0 when updating models.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("NUMBA_CACHE_DIR", str(ROOT / "cache/numba"))
    from inference import main as convert

    convert(SimpleNamespace(source=str(args.source.resolve()), target=str(args.target.resolve()),
                            output=str(args.output.resolve()), diffusion_steps=args.steps,
                            length_adjust=1.0, inference_cfg_rate=0.7, f0_condition=True,
                            auto_f0_adjust=False, semi_tone_shift=args.shift,
                            checkpoint=None, config=None, fp16=True))


if __name__ == "__main__":
    main()
