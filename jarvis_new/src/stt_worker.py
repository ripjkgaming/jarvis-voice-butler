"""Tiny Whisper transcriber for keyword_spot (runs in the main venv).

Protocol on stdin/stdout: <u32 little-endian byte count><int16 16 kHz PCM>
in, one JSON line {"text": ...} out. Loads the model once; exits on EOF.
"""

from __future__ import annotations

import json
import os
import struct
import sys


def main() -> int:
    import numpy as np
    from faster_whisper import WhisperModel

    model = WhisperModel(
        os.environ.get("JARVIS_SPOT_MODEL", "base"),
        device="cpu",
        compute_type="int8",
        cpu_threads=2,
    )
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        head = stdin.read(4)
        if len(head) < 4:
            return 0
        (n,) = struct.unpack("<I", head)
        raw = stdin.read(n)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            segments, _info = model.transcribe(
                audio,
                language="en",
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception:
            text = ""
        stdout.write((json.dumps({"text": text}) + "\n").encode())
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
