"""Identify the running installation-owned song worker that needs GPU memory."""

import json
from pathlib import Path

import psutil

from settings import ROOT


def active_singing_job():
    try:
        job = json.loads((ROOT / "data/songbook/gpu-job.json").read_text(encoding="utf-8"))
        process = psutil.Process(int(job["pid"]))
        expected = (ROOT / "app/song_worker.py").resolve()
        if process.is_running() and any(Path(a).resolve() == expected for a in process.cmdline()[1:] if not a.startswith("-")):
            return job
    except (OSError, ValueError, KeyError, psutil.Error):
        pass
    return None
