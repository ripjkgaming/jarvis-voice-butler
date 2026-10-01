"""Run WhatSie (WhatsApp Web) headless so Jarvis can use WhatsApp with no window.

WhatSie is a Qt app: QT_QPA_PLATFORM=offscreen keeps it fully off screen while
its session stays logged in and the CDP debug port (9223) answers, so every
helper in system/whatsapp.py works unchanged.
"""

from __future__ import annotations

import subprocess
import time

from system import whatsapp as wa

LAUNCH = [
    "flatpak",
    "run",
    "--env=QT_QPA_PLATFORM=offscreen",
    f"--env=QTWEBENGINE_CHROMIUM_FLAGS=--remote-debugging-port={wa.PORT} --no-sandbox",
    "com.ktechpit.whatsie",
]


def ensure(timeout_s: float = 60.0, spawn=subprocess.Popen, sleep=time.sleep) -> bool:
    """True when the WhatsApp page answers, launching it headless if needed."""
    if wa.alive() and wa.page_present():
        return True
    try:
        spawn(
            LAUNCH,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        sleep(2.0)
        if wa.alive() and wa.page_present():
            return True
    return False
