"""Remote-mic hotword uplink for the Jarvis phone app (wake venv only).

The phone streams 16kHz mono int16 PCM over TCP; this server runs the same
openWakeWord `hey_jarvis` model as the local listener and writes back a
`WAKE <unix-ts>` line on detection (10s cooldown). The phone buzzes and
opens its talk screen — no on-device ML, no extra battery burn beyond the
mic itself.

Protocol (all little-endian, all timeouts fail-soft):
    phone -> {"rate": 16000, "channels": 1}\\n   (JSON handshake, one line)
    phone -> raw int16 PCM frames, any chunking
    server -> b"WAKE 1726500000.0\\n"            (per detection, cooldown)

Run: `.venv-wake/bin/python src/mic_uplink.py` (port from
JARVIS_MIC_PORT, default 4318, loopback unless JARVIS_MIC_BIND is set).
Only int16/16k/mono is accepted — anything else gets a JSON error line
and a closed socket. stdlib except openWakeWord (already in .venv-wake).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import struct
import threading
import time

try:
    from wake_client import WAKE_MODEL, frame_16k_chunks, wake_threshold
except ImportError:  # pragma: no cover - package layout fallback
    from src.wake_client import WAKE_MODEL, frame_16k_chunks, wake_threshold

logger = logging.getLogger("jarvis-mic-uplink")

DEFAULT_PORT = 4318
COOLDOWN_S = 10.0
HANDSHAKE_TIMEOUT = 5.0
CHUNK_TIMEOUT = 30.0


def parse_handshake(line: bytes) -> tuple[int, int]:
    """Validate the JSON handshake. Returns (rate, channels) or raises."""
    try:
        spec = json.loads(line.decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("handshake must be one JSON line") from exc
    if not isinstance(spec, dict):
        raise ValueError("handshake must be an object")
    rate, channels = spec.get("rate"), spec.get("channels")
    if rate != 16000 or channels != 1:
        raise ValueError(f"only 16kHz mono supported, got {rate}/{channels}")
    return rate, channels


def load_model():
    """Load the shared hey_jarvis openWakeWord model (import is slow)."""
    from openwakeword.model import Model

    return Model(wakeword_models=[WAKE_MODEL])


def serve_client(conn: socket.socket, addr, model, threshold: float) -> None:
    """One phone session: handshake, stream, WAKE lines. Never raises."""
    peer = f"{addr[0]}:{addr[1]}"
    conn.settimeout(HANDSHAKE_TIMEOUT)
    try:
        line = b""
        while not line.endswith(b"\n"):
            chunk = conn.recv(256)
            if not chunk:
                return
            line += chunk
            if len(line) > 1024:
                raise ValueError("handshake too long")
        parse_handshake(line)
        conn.sendall(b'{"ok": true}\n')
    except (OSError, ValueError) as exc:
        with contextlib.suppress(OSError):
            conn.sendall(
                json.dumps({"ok": False, "error": str(exc)[:120]}).encode() + b"\n"
            )
        return
    logger.info("mic uplink from %s", peer)
    conn.settimeout(CHUNK_TIMEOUT)
    pending: list[int] = []
    last_wake = 0.0
    try:
        while True:
            raw = conn.recv(4096)
            if not raw:
                return
            samples = list(
                struct.unpack(f"<{len(raw) // 2}h", raw[: len(raw) // 2 * 2])
            )
            frames, pending = frame_16k_chunks(pending, samples)
            for frame in frames:
                try:
                    score = float(model.predict(frame).get(WAKE_MODEL, 0.0))
                except Exception:
                    continue
                now = time.time()
                if score >= threshold and now - last_wake >= COOLDOWN_S:
                    last_wake = now
                    conn.sendall(f"WAKE {now}\n".encode())
    except (OSError, TimeoutError):
        pass
    finally:
        logger.info("mic uplink closed %s", peer)


def serve_forever(port: int = DEFAULT_PORT, bind: str = "127.0.0.1") -> None:
    model = load_model()
    threshold = wake_threshold()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((bind, port))
    server.listen(4)
    logger.warning("mic uplink on %s:%d (threshold %.2f)", bind, port, threshold)
    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(
                target=serve_client, args=(conn, addr, model, threshold), daemon=True
            ).start()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    try:
        port = int(os.environ.get("JARVIS_MIC_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    serve_forever(port=port, bind=os.environ.get("JARVIS_MIC_BIND", "127.0.0.1"))


if __name__ == "__main__":
    main()
