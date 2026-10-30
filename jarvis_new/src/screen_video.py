"""Selected-window live input for desktop voice; the bridge owns capture grants."""

from __future__ import annotations

import asyncio
import io
import os
import time
import urllib.request

from jarvis_rt.io import VideoInput
from screen_understanding import local_owner_authorized
from watch_visual import MAX_EDGE, MAX_UPLOAD

JPEG_QUALITY = 85


def read_frame():
    """Return one bounded JPEG (bytes) of the selected window, or None.

    No cached pixels, retries, or unauthenticated capture fallback.
    """
    from PIL import Image

    from wizard.gate import BRIDGE_DEFAULT_PORT, bridge_host, bridge_token

    if not local_owner_authorized():
        return None
    token = bridge_token()
    if not token:
        return None
    port = int(os.environ.get("JARVIS_BRIDGE_PORT", str(BRIDGE_DEFAULT_PORT)))
    request = urllib.request.Request(
        f"http://{bridge_host()}:{port}/watch/video-frame",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=6) as response:
        if response.status != 200:
            return None
        raw = response.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD or not local_owner_authorized():
        return None
    with Image.open(io.BytesIO(raw)) as image:
        if image.width > MAX_EDGE or image.height > MAX_EDGE:
            return None
        # Re-encode so the frame handed to the model is always plain RGB JPEG.
        encoded = io.BytesIO()
        image.convert("RGB").save(encoded, format="JPEG", quality=JPEG_QUALITY)
        return encoded.getvalue()


class ScreenVideoInput(VideoInput):
    """Yields ``(jpeg_bytes, ts_ms)`` at most once per second while attached.

    Frames are never queued: each pull performs a fresh, authorized capture.
    The 2 h / 7200-frame budget is enforced by ``watch_visual.VisualObserver``.
    """

    def __init__(self, *, read=read_frame, authorized=local_owner_authorized):
        super().__init__()
        self._read = read
        self._authorized = authorized
        self._attached = False

    def on_attached(self):
        self._attached = True

    def on_detached(self):
        self._attached = False

    async def frames(self):
        while self._attached:
            # Keep capture off the audio loop and never queue old frames.
            await asyncio.sleep(1)
            if not self._attached:
                break
            if not self._authorized():
                continue
            try:
                frame = await asyncio.to_thread(self._read)
            except Exception:
                # Missing permission, a stopped bridge, or capture failure must
                # never terminate the voice session. The next read is fresh.
                continue
            if self._attached and self._authorized() and frame is not None:
                yield frame, int(time.time() * 1000)
