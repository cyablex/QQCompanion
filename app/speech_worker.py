"""One voice transcription per process; exit releases the local model memory."""

import base64
import json
import sys

from game_mode import VoicePaused
from media import LocalSpeech
from settings import read_settings


def main():
    if read_settings().get("game_mode"):
        raise VoicePaused("Game mode pauses local voice processing")
    data = base64.b64decode(sys.stdin.buffer.read(18 * 1024 * 1024), validate=True)
    text = LocalSpeech().recognize(data)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"text": text}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
