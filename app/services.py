"""Start, inspect, or stop only this installation's helper processes."""

import argparse
import os
import socket
import subprocess

import psutil
from settings import ROOT

SCRIPTS = {
    "voice": "voice_service.py",
    "setup": "setup_server.py",
    "bot": "run_astrbot.py",
    "qq": "run_napcat.py",
    "watchdog": "watchdog.py",
    "login": "startup_monitor.py",
}
LOGS = {
    "voice": "local-voice",
    "setup": "setup",
    "bot": "astrbot",
    "qq": "napcat",
    "watchdog": "watchdog",
    "login": "startup-monitor",
}


def qq_webui_running():
    try:
        with socket.create_connection(("127.0.0.1", 6099), timeout=1):
            return True
    except OSError:
        return False


def matching():
    result = {}
    exe = str(ROOT / "runtime/python.exe").lower()
    for process in psutil.process_iter(["pid", "exe"]):
        try:
            if str(process.info["exe"]).lower() != exe.replace("/", "\\"):
                continue
            args = [a.replace("\\", "/").lower() for a in process.cmdline()]
            for name, script in SCRIPTS.items():
                expected = str(ROOT / "app" / script).replace("\\", "/").lower()
                if expected in args:
                    result[name] = process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return result


def start(names):
    names = list(names)
    if "bot" in names and "voice" not in names:
        names.insert(0, "voice")
    elif "voice" in names:
        names = ["voice", *[name for name in names if name != "voice"]]
    if (ROOT / "config/offline-development.json").exists():
        blocked = [name for name in names if name != "setup"]
        if blocked:
            print("Offline development: QQ login and online services are disabled.")
        names = [name for name in names if name == "setup"]
        if not names:
            return
    processes = matching()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["QQCOMPANION_ROOT"] = str(ROOT)
    for name in names:
        if name in processes:
            print(name, "already running")
            continue
        if name == "qq" and qq_webui_running():
            print("qq control service already available")
            continue
        script = SCRIPTS[name]
        with (ROOT / f"logs/{LOGS[name]}.log").open("ab") as log:
            subprocess.Popen(
                [str(ROOT / "runtime/python.exe"), str(ROOT / "app" / script)],
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW
                | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        print(name, "started")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["status", "start", "stop"])
    parser.add_argument("services", nargs="*", choices=list(SCRIPTS))
    args = parser.parse_args()
    processes = matching()
    if args.command == "status":
        for name in SCRIPTS:
            print(name, "running" if name in processes else "stopped")
        return
    if args.command == "stop":
        names = args.services or ["watchdog", "login", "bot", "voice", "qq", "setup"]
        for name in names:
            process = processes.get(name)
            if process is None:
                continue
            try:
                children = process.children(recursive=True)
                for child in reversed(children):
                    try:
                        child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                process.terminate()
                try:
                    process.wait(timeout=10)
                except psutil.TimeoutExpired:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
        print("Stopped helper processes belonging to this installation.")
        return
    start(args.services or list(SCRIPTS))


if __name__ == "__main__":
    main()
