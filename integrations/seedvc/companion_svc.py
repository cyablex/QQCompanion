"""Local Seed-VC singing conversion entry; reference voice alone is not a song."""

import argparse
import json
import math
import os
import sys
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(os.environ.get('SEEDVC_ROOT', Path(__file__).resolve().parents[2].parent / 'SeedVC'))


def main():
    parser = argparse.ArgumentParser(description="把实际唱出的干声转换为大肥鱼的日常参考音色")
    parser.add_argument("--source", type=Path, required=True, help="已有旋律和歌词的清唱/歌曲干声，不能用朗读代替")
    parser.add_argument("--target", type=Path, default=ROOT.parent / "GPTSoVITS/reference/yui/everyday.wav")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs")
    parser.add_argument("--steps", type=int, choices=range(10, 61), default=50)
    parser.add_argument('--cfg-rate', type=float, default=0.9, help='Seed-VC参考引导强度，0到1')
    parser.add_argument("--shift", type=int, choices=range(-12, 13), default=0)
    parser.add_argument("--pitch-mode", choices=("reference_octave", "preserve"), default="reference_octave")
    args = parser.parse_args()
    if not math.isfinite(args.cfg_rate) or not 0 <= args.cfg_rate <= 1:
        parser.error('参考引导强度必须在0到1之间')
    if not args.source.is_file() or not args.target.is_file():
        parser.error("需要实际存在的歌声干声与参考音色文件")
    config = Path(os.environ.get("QQCOMPANION_ROOT", ROOT.parent / "QQCompanion")) / "config/user.json"
    if config.exists() and json.loads(config.read_text(encoding="utf-8")).get("game_mode"):
        parser.error("游戏模式已开启，请先关闭游戏模式")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    import inference
    if args.pitch_mode == "reference_octave":
        import numpy as np
        from pitch_policy import reference_register

        load_models = inference.load_models

        def load_with_register(options):
            models = list(load_models(options))
            extract = models[2]
            reference = None

            def pitch(audio, **kwargs):
                nonlocal reference
                result = extract(audio, **kwargs)
                # Seed-VC extracts target/reference first, then the source vocals.
                if reference is None:
                    reference = result
                    return result
                adjusted, report = reference_register(result, reference)
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / "pitch.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                return np.asarray(adjusted, dtype=result.dtype)

            models[2] = pitch
            return tuple(models)

        inference.load_models = load_with_register

    inference.main(SimpleNamespace(source=str(args.source.resolve()), target=str(args.target.resolve()),
                            output=str(args.output.resolve()), diffusion_steps=args.steps,
                            length_adjust=1.0, inference_cfg_rate=args.cfg_rate, f0_condition=True,
                            auto_f0_adjust=False, semi_tone_shift=args.shift,
                            checkpoint=None, config=None, fp16=True))


if __name__ == "__main__":
    main()
