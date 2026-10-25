"""Recover companion helper crashes while this installation is running."""

import ctypes
import hashlib
import json
import subprocess
import time

from connection_alerts import ConnectionAlerts, connection_phase
from qq_readiness import recovery_due
from services import matching, qq_webui_running, start
from settings import ROOT, atomic_json, read_settings


def main():
    if (ROOT / "config/offline-development.json").exists():
        return
    # A per-installation Windows mutex prevents two independent supervisors.
    tag = hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:16]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, True, f"Local\\QQCompanion_{tag}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        return
    last_start = {}
    alerts = ConnectionAlerts()
    unhealthy_since = 0
    try:
        attempts = json.loads((ROOT / "data/supervisor.json").read_text(encoding="utf-8")).get("session_restart_times", [])
    except (OSError, ValueError):
        attempts = []
    try:
        while True:
            processes = matching()
            now = time.time()
            for name in ["voice", "setup", "bot", "login", "qq"]:
                if name == "qq" and qq_webui_running():
                    continue
                if name not in processes and now - last_start.get(name, 0) > 120:
                    start([name])
                    last_start[name] = now
            health = {}
            try:
                health = json.loads(
                    (ROOT / "data/health.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass
            try:
                startup = json.loads((ROOT / "data/startup.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                startup = {}
            phase = startup.get("phase") if 0 <= now - startup.get("updated", 0) < 45 else None
            try:
                cfg = read_settings()
                alerts.tick(
                    connection_phase(startup, health, "bot" in processes, now),
                    now, enabled=cfg.get("live", False) and not cfg.get("paused", False),
                )
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                # Desktop notification failures must not stop QQ recovery.
                print("Connection notice failed:", type(exc).__name__, flush=True)
            recover, unhealthy_since, attempts = recovery_due(phase, now, unhealthy_since, attempts)
            if recover:
                # Restart only this installation's QQ helper, never the user's
                # independent desktop QQ or a session awaiting phone approval.
                subprocess.run(
                    [str(ROOT / "runtime/python.exe"), str(ROOT / "app/services.py"), "stop", "qq"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW, check=True,
                )
                start(["qq"])
                attempts.append(now)
                unhealthy_since = now
            atomic_json(
                ROOT / "data/supervisor.json",
                {
                    "updated": now,
                    "bot_running": "bot" in processes,
                    "setup_running": "setup" in processes,
                    "qq_helper_running": "qq" in processes,
                    "qq_connected": health.get("qq_connected", False),
                    "session_restart_times": attempts,
                    "session_recovery_limited": phase in {"session_unavailable", "recovery_failed"} and len(attempts) >= 2,
                },
            )
            time.sleep(15)
    finally:
        kernel.ReleaseMutex(ctypes.c_void_p(handle))
        kernel.CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    main()
