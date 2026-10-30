"""Strip TTS delivery markup (angle-bracket tags) from transcript text."""

from __future__ import annotations

import re

_TAG = re.compile(r"</?[A-Za-z][^<>]*?/?>")


def strip_all_markup(text: str) -> str:
    # every markup shape is angle-bracketed: skip the scan for the common case
    if "<" not in text:
        return text
    return _TAG.sub("", text)
