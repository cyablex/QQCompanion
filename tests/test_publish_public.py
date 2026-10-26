import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from publish_public import sync_snapshot  # noqa: E402


def test_snapshot_removes_obsolete_exports_and_preserves_public_history(tmp_path):
    snapshot, checkout = tmp_path / "snapshot", tmp_path / "repository"
    snapshot.mkdir()
    checkout.mkdir()
    (checkout / ".git").mkdir()
    (checkout / ".git/HEAD").write_text("public-history")
    (checkout / "PUBLIC_SOURCE.json").write_text(json.dumps({"files": ["old.py", "keep.py"]}))
    (checkout / "old.py").write_text("obsolete")
    (checkout / "keep.py").write_text("old")
    (checkout / "CONTRIBUTING.md").write_text("remote addition")
    (snapshot / "keep.py").write_text("new")
    (snapshot / "PUBLIC_SOURCE.json").write_text(json.dumps({"files": ["keep.py"]}))
    sync_snapshot(snapshot, checkout)
    assert not (checkout / "old.py").exists()
    assert (checkout / "keep.py").read_text() == "new"
    assert (checkout / "CONTRIBUTING.md").read_text() == "remote addition"
    assert (checkout / ".git/HEAD").read_text() == "public-history"


@pytest.mark.parametrize("name", ["../outside.txt", ".git/HEAD"])
def test_snapshot_rejects_manifest_paths_outside_public_source(tmp_path, name):
    snapshot, checkout = tmp_path / "snapshot", tmp_path / "repository"
    snapshot.mkdir()
    checkout.mkdir()
    (checkout / "PUBLIC_SOURCE.json").write_text(json.dumps({"files": [name]}))
    with pytest.raises(ValueError, match="manifest path"):
        sync_snapshot(snapshot, checkout)
