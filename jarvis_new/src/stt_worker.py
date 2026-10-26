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
    # Keep local transcription from competing with the desktop, wake-word
    # capture, or video work when the host is busy. Apply before model
    # libraries create their worker threads; 0 opts out of this policy.
    if hasattr(os, "nice"):
        try:
            priority = max(0, min(19, int(os.environ.get("JARVIS_SPOT_NICE", "5"))))
            os.nice(priority)
        except (OSError, ValueError):
            pass

    import numpy as np
    from faster_whisper import WhisperModel

    from whisper_config import configured_model

    # base.en + 4 threads + no timestamps: a 3.5 s school-wake question
    # went from ~1.2 s to ~0.6 s on the i5-13420H with the same text.
    model = WhisperModel(
        configured_model("JARVIS_SPOT_MODEL"),
        device="cpu",
        compute_type="int8",
        cpu_threads=int(os.environ.get("JARVIS_SPOT_THREADS", "4")),
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
                task="transcribe",
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
                # Bias toward the wake name: tiny Whisper heard Sir's "hey
                # Jarvis" as "he's nervous" and school mode ignored him.
                hotwords="Jarvis",
            )
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception:
            text = ""
        stdout.write((json.dumps({"text": text}) + "\n").encode())
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
