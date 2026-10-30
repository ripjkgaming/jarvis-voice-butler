"""Prefetch the local voice models so the first call does not download them.

Covers the cloud-free ears and voice used by the `direct` pipeline and the
Dockerfile build:

- faster-whisper model (``JARVIS_WHISPER_MODEL``, default ``base.en``) into
  the Hugging Face cache (``HF_HOME``).
- Piper voice ``en_GB-alan-medium`` (onnx + json) into ``~/.jarvis/voices``.
- Silero VAD ONNX, which ships in the repo at
  ``src/jarvis_rt/resources/silero_vad.onnx``; this is only verified.

Offline-safe: network failures are reported as warnings and do not fail the
run, because the runtime fetches missing models lazily. A missing bundled
Silero file is a checkout error and exits 1. No secrets are read or sent.

Usage: ``uv run python scripts/prefetch_models.py``
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SILERO_PATH = REPO_ROOT / "src" / "jarvis_rt" / "resources" / "silero_vad.onnx"
SILERO_MIN_BYTES = 1_000_000

PIPER_VOICE = "en_GB-alan-medium"
PIPER_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/"
)
PIPER_FILES = (f"{PIPER_VOICE}.onnx", f"{PIPER_VOICE}.onnx.json")
NETWORK_TIMEOUT_S = 30

# Mirrors src/audio_shared.py and src/local_voice.py (Path.home() based).
VOICE_DIR = Path.home() / ".jarvis" / "voices"


def _warn(msg: str) -> None:
    print(f"[prefetch] WARN: {msg}", file=sys.stderr)


def check_silero() -> bool:
    """Verify the bundled Silero VAD model is present and plausibly sized."""
    if SILERO_PATH.is_file() and SILERO_PATH.stat().st_size >= SILERO_MIN_BYTES:
        print(f"[prefetch] silero ok: {SILERO_PATH}")
        return True
    print(f"[prefetch] ERROR: bundled Silero VAD missing: {SILERO_PATH}")
    return False


def prefetch_whisper() -> None:
    """Fetch the faster-whisper CTranslate2 model into the HF cache."""
    model = os.environ.get("JARVIS_WHISPER_MODEL", "").strip() or "base.en"
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        _warn("faster-whisper is not installed; skipping whisper prefetch")
        return
    try:
        try:
            path = download_model(model, local_files_only=True)
            print(f"[prefetch] whisper {model} already cached: {path}")
            return
        except Exception:  # not cached yet (or cache unreadable): fetch it
            pass
        path = download_model(model)
        print(f"[prefetch] whisper {model} downloaded: {path}")
    except Exception as exc:  # network down, hub error, disk full, ...
        _warn(f"whisper {model} not prefetched ({type(exc).__name__}: {exc})")


def _fetch(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with (
        urllib.request.urlopen(url, timeout=NETWORK_TIMEOUT_S) as resp,
        tmp.open("wb") as out,
    ):
        while chunk := resp.read(1 << 16):
            out.write(chunk)
    tmp.replace(dest)


def prefetch_piper() -> None:
    """Download the Piper voice (onnx + json) if it is not already present."""
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    for name in PIPER_FILES:
        dest = VOICE_DIR / name
        if dest.is_file() and dest.stat().st_size > 0:
            print(f"[prefetch] piper {name} already present: {dest}")
            continue
        try:
            _fetch(f"{PIPER_BASE}{name}", dest)
            print(f"[prefetch] piper {name} downloaded: {dest}")
        except Exception as exc:  # offline or HF unavailable
            _warn(f"piper {name} not prefetched ({type(exc).__name__}: {exc})")


def main() -> int:
    ok = check_silero()
    prefetch_whisper()
    prefetch_piper()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
