"""Synthetic, non-sensitive screen frames for Always Aware tests and the spike.

Rendered with PIL: a terminal showing a pytest failure, a half-filled web
form, a static document, and a password dialog. No real screen content.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080


def _font(size: int):
    for name in (
        "/usr/share/fonts/liberation-mono-fonts/LiberationMono-Regular.ttf",
        "/usr/share/fonts/liberation-mono/LiberationMono-Regular.ttf",
        "/usr/share/fonts/dejavu-sans-mono-fonts/DejaVuSansMono.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


PYTEST_LINES = [
    "$ uv run pytest tests/test_auth.py",
    "============================= test session starts ==============================",
    "collected 14 items",
    "",
    "tests/test_auth.py ..........FF..                                        [100%]",
    "",
    "=================================== FAILURES ===================================",
    "_______________________________ test_token_refresh _____________________________",
    "",
    "    def test_token_refresh():",
    ">       session = make_session()",
    "E       NameError: name 'make_session' is not defined",
    "",
    "tests/test_auth.py:41: NameError",
    "=========================== short test summary info ============================",
    "FAILED tests/test_auth.py::test_token_refresh - NameError: name 'make_session'",
    "FAILED tests/test_auth.py::test_logout - NameError: name 'make_session' is not",
    "========================= 2 failed, 12 passed in 0.41s =========================",
]


def terminal(step: int = 5) -> np.ndarray:
    """Konsole-like window; lines appear over the burst (step 0..5)."""
    img = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 34], fill=(45, 45, 52))
    d.text(
        (14, 8),
        "pytest — Konsole — ~/code/demo_app",
        fill=(220, 220, 220),
        font=_font(18),
    )
    font = _font(22)
    shown = PYTEST_LINES[: 4 + int(step * (len(PYTEST_LINES) - 4) / 5)]
    for i, line in enumerate(shown):
        color = (
            (255, 110, 110)
            if line.startswith(("E ", "FAILED", ">"))
            else (210, 230, 210)
        )
        d.text((24, 60 + i * 30), line, fill=color, font=font)
    return np.array(img)[:, :, ::-1].copy()


def form(step: int = 5) -> np.ndarray:
    img = Image.new("RGB", (W, H), (245, 245, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 80], fill=(60, 90, 160))
    d.text((30, 22), "Example Council — Report a pothole", fill="white", font=_font(32))
    labels = ["Your name", "Street", "Postcode", "Description"]
    values = ["Alex Example", "1 Test Road", "", "Large pothole near the bus stop"]
    font = _font(26)
    for i, (lab, val) in enumerate(zip(labels, values)):
        y = 160 + i * 140
        d.text((200, y), lab, fill=(30, 30, 30), font=font)
        d.rectangle(
            [200, y + 40, 1100, y + 95], outline=(120, 120, 120), width=2, fill="white"
        )
        if i <= step:
            d.text((215, y + 52), val, fill=(20, 20, 20), font=font)
    d.rectangle([200, 760, 420, 830], fill=(40, 140, 60))
    d.text((250, 778), "Submit", fill="white", font=font)
    return np.array(img)[:, :, ::-1].copy()


def document(step: int = 0) -> np.ndarray:
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    font = _font(24)
    d.text((300, 80), "Chapter 3: Rivers and erosion", fill=(0, 0, 0), font=_font(40))
    for i in range(20):
        d.text(
            (300, 180 + i * 40),
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
            fill=(40, 40, 40),
            font=font,
        )
    return np.array(img)[:, :, ::-1].copy()


def password_dialog(step: int = 0) -> np.ndarray:
    img = Image.new("RGB", (W, H), (30, 30, 30))
    d = ImageDraw.Draw(img)
    d.rectangle([660, 340, 1260, 740], fill=(240, 240, 240))
    d.text((700, 370), "KeePassXC - Unlock Database", fill=(0, 0, 0), font=_font(28))
    d.text((700, 460), "Password:", fill=(0, 0, 0), font=_font(24))
    d.rectangle([700, 500, 1220, 550], outline=(0, 0, 0), width=2, fill="white")
    return np.array(img)[:, :, ::-1].copy()


SCENES = {
    "terminal": terminal,
    "form": form,
    "document": document,
    "password": password_dialog,
}


def write_burst(
    scene: str, out_dir: Path, key: str = "m0", edge: int = 1280
) -> list[Path]:
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(6):
        img = SCENES[scene](i)
        h, w = img.shape[:2]
        s = edge / max(h, w)
        img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        p = out_dir / f"{key}_{i:02d}.jpg"
        cv2.imwrite(str(p), img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        paths.append(p)
    return paths
