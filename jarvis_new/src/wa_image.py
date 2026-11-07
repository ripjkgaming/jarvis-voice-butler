"""Validated local PNG attachments for WhatsApp drafts, never a send authority."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

MAX_IMAGE_BYTES = 12 * 1024 * 1024


def inspect_png(path: str) -> dict:
    file = Path(path).expanduser().resolve(strict=True)
    if not file.is_file() or file.suffix.lower() != ".png":
        raise ValueError("Only an existing PNG file can be attached.")
    if file.stat().st_size > MAX_IMAGE_BYTES or file.stat().st_size < 45:
        raise ValueError("PNG must be between 45 bytes and 12 MB.")
    data = file.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("The selected file is not a PNG.")
    width, height = struct.unpack(">II", data[16:24])
    if not (0 < width <= 16384 and 0 < height <= 16384):
        raise ValueError("Invalid PNG dimensions.")
    from PIL import Image

    with Image.open(file) as im:
        im.verify()
    return {
        "path": str(file),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "width": width,
        "height": height,
    }


def verify_image(image: dict) -> bool:
    try:
        current = inspect_png(image["path"])
        return all(
            current[k] == image[k] for k in ("sha256", "size", "width", "height")
        )
    except (OSError, ValueError, KeyError):
        return False
