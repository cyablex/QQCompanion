import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from archive_crypto import decrypt_file  # noqa: E402
from resource_archive import export_resources, restore_resources  # noqa: E402

PASSWORD = "test-resources-password-123"


@pytest.fixture
def resources(tmp_path):
    root = tmp_path / "old" / "QQCompanion"
    for relative in ("runtime", "astrbot", "napcat", "models", "frontend/dist"):
        folder = root / relative
        folder.mkdir(parents=True)
        (folder / "component.txt").write_text("installed component")
    base = root.parent / "base-python311"
    base.mkdir()
    (base / "python.exe").write_bytes(b"base interpreter")
    (root / "runtime/python.exe").write_bytes(b"core interpreter")
    (root / "astrbot/data").mkdir()
    (root / "astrbot/data/private.json").write_text("not in resources")
    (root / "napcat/config").mkdir()
    (root / "napcat/config/webui.json").write_text("login token")
    tts = root.parent / "GPTSoVITS"
    (tts / "app").mkdir(parents=True)
    (tts / "app/model.bin").write_bytes(b"model")
    (tts / "launch.py").write_text("# generic entry")
    for folder, home in [
        (root.parent / "SeedVC/.venv", base),
        (root.parent / "QQCompanionFrontendTools/python-env", root / "runtime"),
    ]:
        folder.mkdir(parents=True)
        (folder / "pyvenv.cfg").write_text(
            f"home = {home}\ninclude-system-site-packages = true\nversion = 3.11.16\n",
            encoding="utf-8",
        )
    return root


def test_resources_exclude_login_data_and_repair_both_venvs(resources, tmp_path):
    archive = tmp_path / "offline.dfyresources"
    export_resources(resources, archive, PASSWORD)
    clear = tmp_path / "decoded.zip"
    decrypt_file(archive, clear, PASSWORD)
    with zipfile.ZipFile(clear) as zipped:
        assert not any(
            "private.json" in n or "webui.json" in n for n in zipped.namelist()
        )
        assert json.loads(zipped.read("manifest.json"))["kind"] == "offline-resources"
    target = tmp_path / "new" / "QQCompanion"
    assert restore_resources(archive, target, PASSWORD)["restored"]
    svc = target.parent / "SeedVC/.venv/pyvenv.cfg"
    gui = target.parent / "QQCompanionFrontendTools/python-env/pyvenv.cfg"
    assert str(target.parent / "QQCompanionRuntime311") in svc.read_text(
        encoding="utf-8"
    )
    assert str(target / "runtime") in gui.read_text(encoding="utf-8")
    assert str(resources.parent) not in svc.read_text(encoding="utf-8")
    assert str(resources.parent) not in gui.read_text(encoding="utf-8")


def test_resources_restore_refuses_existing_components(resources, tmp_path):
    archive = tmp_path / "offline.dfyresources"
    export_resources(resources, archive, PASSWORD)
    with pytest.raises(ValueError, match="已有运行资源"):
        restore_resources(archive, resources, PASSWORD)
    assert (resources / "runtime/python.exe").read_bytes() == b"core interpreter"


def test_wrong_resources_password_leaves_destination_uninstalled(resources, tmp_path):
    archive = tmp_path / "offline.dfyresources"
    export_resources(resources, archive, PASSWORD)
    target = tmp_path / "new" / "QQCompanion"
    with pytest.raises(ValueError, match="口令错误"):
        restore_resources(archive, target, "wrong-resource-password")
    assert not target.exists()


def test_resource_restore_checks_free_space_before_decryption(
    resources, tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import resource_archive

    archive = tmp_path / "offline.dfyresources"
    export_resources(resources, archive, PASSWORD)
    monkeypatch.setattr(
        resource_archive.shutil, "disk_usage", lambda path: SimpleNamespace(free=0)
    )
    with pytest.raises(ValueError, match="空间不足"):
        restore_resources(archive, tmp_path / "new/QQCompanion", PASSWORD)
