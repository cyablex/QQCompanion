"""Display non-secret readiness and quota information."""

import json
import socket

from settings import ROOT, read_settings

if __name__ == "__main__":
    cfg = read_settings()
    print(
        "Configured:",
        bool(
            cfg["owner_qq"]
            and cfg["bot_qq"]
            and (ROOT / "config/api-key.dpapi").exists()
        ),
    )
    print("Sending enabled:", cfg["live"], "Paused:", cfg["paused"])
    for port in [8765, 6185, 6199, 6099]:
        with socket.socket() as sock:
            print("Port", port, "ready:", sock.connect_ex(("127.0.0.1", port)) == 0)
    p = ROOT / "data/health.json"
    if p.exists():
        print(
            json.dumps(
                json.loads(p.read_text(encoding="utf-8")), ensure_ascii=False, indent=2
            )
        )
