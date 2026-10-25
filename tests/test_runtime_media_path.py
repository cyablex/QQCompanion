import importlib.util
import os
from pathlib import Path


def test_runtime_uses_existing_ffmpeg_without_changing_global_installation(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / "app/run_astrbot.py"
    monkeypatch.syspath_prepend(str(source.parent))
    spec = importlib.util.spec_from_file_location("companion_runtime_media_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "QQCompanion"
    directory = tmp_path / "GPTSoVITS/app/existing/runtime"
    directory.mkdir(parents=True)
    (directory / "ffmpeg.exe").touch()
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setenv("PATH", "original-path")
    assert module.configure_media_path() == str(directory)
    assert os.environ["PATH"] == str(directory) + os.pathsep + "original-path"
