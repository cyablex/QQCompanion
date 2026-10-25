import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from public_release import audit_tree, sanitise  # noqa: E402


def test_public_sanitisation_replaces_identifiers_consistently_and_drops_old_sys_path():
    source = 'sys.path.insert(0, r"D:\\Private\\app")\nowner = "777777777"\nassert owner == "777777777"\n'
    clean = sanitise(source, {"777777777": "900000001"})
    assert "D:" not in clean and "777777777" not in clean
    assert clean.count("900000001") == 2


def test_public_identity_replacement_does_not_corrupt_a_public_github_handle():
    assert (
        sanitise("identity:'demo'; https://github.com/demox", {"demo": "本机用户"})
        == "identity:'本机用户'; https://github.com/demox"
    )


@pytest.mark.parametrize(
    "name,content,rule",
    [
        ("README.md", "do not publish personal-value", "private-value"),
        ("config.json", "ghp_" + "a" * 30, "credential-pattern"),
        ("photo.png", "anything", "unreviewed-file-type"),
    ],
)
def test_public_audit_blocks_private_content_credentials_and_unreviewed_media(
    tmp_path, name, content, rule
):
    (tmp_path / name).write_text(content, encoding="utf-8")
    assert audit_tree(tmp_path, {"personal-value"}) == [dict(file=name, rule=rule)]


def test_source_installer_cannot_copy_private_directories(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
    from restore_bundle import install_public_source

    source = tmp_path / "source"
    source.mkdir()
    (source / "PUBLIC_SOURCE.json").write_text(
        json.dumps(dict(format=1, files=["config/user.json"]))
    )
    with pytest.raises(ValueError):
        install_public_source(source, tmp_path / "target")


def test_source_installer_preserves_existing_different_source(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
    from restore_bundle import install_public_source

    source, target = tmp_path / "source", tmp_path / "target"
    for folder in (source, target):
        (folder / "app").mkdir(parents=True)
    (source / "app/engine.py").write_text("new")
    (target / "app/engine.py").write_text("existing")
    (source / "PUBLIC_SOURCE.json").write_text(
        json.dumps(dict(format=1, files=["app/engine.py"]))
    )
    with pytest.raises(ValueError):
        install_public_source(source, target)
    assert (target / "app/engine.py").read_text() == "existing"


def test_clean_runtime_copies_standard_python_without_host_packages(tmp_path):
    from bootstrap import copy_python_base

    base, target = tmp_path / "base", tmp_path / "new-runtime"
    (base / "Lib/site-packages").mkdir(parents=True)
    (base / "Lib/stdlib.py").write_text("standard library")
    (base / "Lib/site-packages/host_package.py").write_text("host-only")
    (base / "python.exe").write_bytes(b"interpreter")
    copy_python_base(base, target)
    assert (target / "python.exe").is_file()
    assert (target / "Lib/stdlib.py").is_file()
    assert not (target / "Lib/site-packages").exists()


def test_bootstrap_help_works_with_a_non_chinese_console():
    script = Path(__file__).resolve().parents[1] / "tools/bootstrap.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"},
        timeout=10,
    )
    assert result.returncode == 0
    assert "准备新的大肥鱼安装" in result.stdout.decode("utf-8")
