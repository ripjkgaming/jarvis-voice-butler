"""Voice + wake-word model acquisition (Piper voice, OWW resources).

Piper voice: en_GB-alan-medium onnx + json, downloaded once into
``~/.jarvis/voices`` (61MB total). The wake-word models ship inside the
openwakeword pip package's resources (hey_jarvis + melspectrogram +
silero_vad), so the "download" step here verifies presence and reports
the expected locations rather than fetching anything.

Pure builders + a thin download helper. Fails soft: a missing model
degrades the models step, not the whole wizard.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

# Piper voice on HuggingFace (rhasspy/piper-voices, main branch).
PIPER_VOICE_NAME = "en_GB-alan-medium"
PIPER_VOICE_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    f"en/en_GB/alan/medium/{PIPER_VOICE_NAME}"
)
PIPER_VOICE_URL = f"{PIPER_VOICE_BASE}.onnx"
PIPER_VOICE_JSON_URL = f"{PIPER_VOICE_BASE}.onnx.json"

DEFAULT_VOICE_DIR = Path.home() / ".jarvis" / "voices"

# openwakeword resources ship inside the package; names are the files the
# wake client loads at runtime (ONNX inference framework only).
OWW_RESOURCES = (
    "hey_jarvis_v0.1.onnx",
    "melspectrogram.onnx",
    "silero_vad.onnx",
    "embedding_model.onnx",
)


def voice_dir(jarvis_home: Path | str | None = None) -> Path:
    """$JARVIS_VOICE_MODEL dir override > $JARVIS_HOME/voices > default. Pure."""
    override = os.environ.get("JARVIS_VOICE_MODEL", "").strip()
    if override:
        p = Path(override.split(":")[0]).expanduser()
        return p.parent
    if jarvis_home is not None:
        return Path(jarvis_home) / "voices"
    home = os.environ.get("JARVIS_HOME", "").strip()
    return (Path(home) / "voices") if home else DEFAULT_VOICE_DIR


def piper_paths(dir_: Path) -> tuple[Path, Path]:
    return (dir_ / f"{PIPER_VOICE_NAME}.onnx", dir_ / f"{PIPER_VOICE_NAME}.onnx.json")


def piper_present(dir_: Path | None = None) -> bool:
    onnx, js = piper_paths(dir_ or voice_dir())
    return onnx.is_file() and onnx.stat().st_size > 1_000_000 and js.is_file()


def oww_resource_dir(site_packages: Path | None = None) -> Path | None:
    """Location of openwakeword's packaged resources, or None. Pure-ish."""
    if site_packages is not None:
        cand = site_packages / "openwakeword" / "resources" / "models"
        return cand if cand.is_dir() else None
    # Lazy import: steps owns repo discovery; top-level would risk a cycle
    # (steps lazily imports this module in its checks).
    from wizard.steps import repo_root

    # Probe the checkout's venvs for any python3.x layout (wake is 3.11,
    # main is 3.14 — never hardcode one).
    for base in (repo_root() / ".venv-wake", repo_root() / ".venv"):
        for sp in sorted(base.glob("lib/python3*/site-packages")):
            cand = sp / "openwakeword" / "resources" / "models"
            if cand.is_dir():
                return cand
    return None


def oww_present(site_packages: Path | None = None) -> bool:
    base = oww_resource_dir(site_packages)
    if base is None:
        return False
    return all((base / name).is_file() for name in OWW_RESOURCES)


def download_piper(dir_: Path) -> tuple[Path, Path]:
    """Download the voice onnx + json into ``dir_`` (idempotent). I/O.

    Returns the two paths. Raises on network failure.
    """
    dir_.mkdir(parents=True, exist_ok=True)
    onnx, js = piper_paths(dir_)
    if not onnx.is_file():
        urllib.request.urlretrieve(PIPER_VOICE_URL, onnx)
    if not js.is_file():
        urllib.request.urlretrieve(PIPER_VOICE_JSON_URL, js)
    return onnx, js


def model_summary() -> dict[str, bool | str]:
    """Wizard-visible model checklist. Pure-ish."""
    return {
        "voice": PIPER_VOICE_NAME,
        "voice_present": piper_present(),
        "voice_dir": str(voice_dir()),
        "oww_present": oww_present(),
        "oww_resources": list(OWW_RESOURCES),
    }
