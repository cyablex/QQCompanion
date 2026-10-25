"""Start the companion; show actual readiness for manual launches."""

import os
import socket
import sys
import time

from services import SCRIPTS, start
from settings import ROOT, atomic_json


def main():
    if (ROOT / "config/offline-development.json").exists():
        start(["setup"])
        if "--auto" not in sys.argv:
            print("当前为离线开发模式，不会启动 QQ 或扫码登录。恢复需手动确认。")
        return
    start(list(SCRIPTS))
    if "--auto" in sys.argv:
        return
    for _ in range(20):
        try:
            with socket.create_connection(("127.0.0.1", 8765), timeout=1):
                pass
            break
        except OSError:
            time.sleep(1)
    atomic_json(ROOT / "data/login-page-opened.json", dict(updated=time.time()))
    os.startfile("http://127.0.0.1:8765/startup")
    print(
        "已打开启动状态页。QQ 登录并连接成功后才会显示就绪；需要扫码时页面会显示二维码。"
    )


if __name__ == "__main__":
    main()
