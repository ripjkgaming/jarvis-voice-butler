"""English-only Whisper selection shared by local voice entry points."""

from __future__ import annotations

import logging
import os

DEFAULT_MODEL = "base.en"
ENGLISH_MODELS = {
    "tiny.en",
    "base.en",
    "small.en",
    "medium.en",
    "distil-small.en",
    "distil-medium.en",
}


def english_model_name(value: str | None) -> str:
    name = (value or "").strip() or DEFAULT_MODEL
    if name in {"tiny", "base", "small", "medium"}:
        return name + ".en"
    if name in ENGLISH_MODELS:
        return name
    logging.getLogger("jarvis-local-stt").warning(
        "Whisper model %r has no supported English-only equivalent; using %s",
        name,
        DEFAULT_MODEL,
    )
    return DEFAULT_MODEL


def configured_model(variable: str = "JARVIS_WHISPER_MODEL") -> str:
    return english_model_name(os.environ.get(variable))
