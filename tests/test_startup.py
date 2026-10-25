"""Reproduce frameworks that cache configuration at first import."""

import shutil
import subprocess
import sys
from pathlib import Path


def test_runner_imports_framework_only_after_preparation_finishes(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "astrbot").mkdir()
    source = Path(__file__).resolve().parents[1] / "app/run_astrbot.py"
    runner = tmp_path / "app/run_astrbot.py"
    shutil.copyfile(source, runner)
    (tmp_path / "value.txt").write_text("old", encoding="utf-8")
    (tmp_path / "app/settings.py").write_text(
        "from pathlib import Path\nROOT=Path(__file__).resolve().parents[1]\ndef load_key(*, relay=False): return 'test-placeholder'\n",
        encoding="utf-8",
    )
    (tmp_path / "astrbot/cached_framework.py").write_text(
        "from settings import ROOT\nVALUE=(ROOT/'value.txt').read_text()\n",
        encoding="utf-8",
    )
    (tmp_path / "app/prepare_runtime.py").write_text(
        "import sys\nfrom settings import ROOT\nsys.path.insert(0,str(ROOT/'astrbot'))\n"
        "def prepare():\n import cached_framework\n (ROOT/'value.txt').write_text('new')\n"
        "if __name__=='__main__': prepare()\n",
        encoding="utf-8",
    )
    (tmp_path / "astrbot/main.py").write_text(
        "import cached_framework\nprint('RUNTIME_CONFIG='+cached_framework.VALUE)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(runner)], capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 0, result.stderr
    assert "RUNTIME_CONFIG=new" in result.stdout
