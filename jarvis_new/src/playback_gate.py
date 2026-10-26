"""One local PCM speaker owner, with durable turn cancellation and a short fade.

All device writes happen on the caller's audio worker. Cancellation only sets
an event: it never closes/aborts a device concurrently with a native write.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import threading
import time
import uuid

import numpy as np

FRAME_S = 0.02
FADE_S = 0.15


class PlaybackSession:
    def __init__(self, gate, turn_id):
        self.gate = gate
        self.turn_id = turn_id
        self.cancelled = threading.Event()
        self._acquired = False
        self._faded = False
        self._tail = b""
        self._lock_fd = None

    def acquire(self):
        while not self.cancelled.is_set():
            if self.gate._output.acquire(timeout=0.02):
                self._acquired = True
                try:
                    path = os.environ.get("JARVIS_SPEAKER_LOCK") or os.path.join(
                        tempfile.gettempdir(), f"jarvis-speaker-{os.getuid()}.lock"
                    )
                    self._lock_fd = os.open(
                        path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
                    )
                    while not self.cancelled.is_set():
                        try:
                            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            return True
                        except BlockingIOError:
                            time.sleep(0.02)
                except BaseException:
                    self.release()
                    raise
                self.release()
        return False

    def release(self):
        if self._acquired:
            self._acquired = False
            if self._lock_fd is not None:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
                self._lock_fd = None
            self.gate._output.release()

    def close(self):
        self.release()
        with self.gate._lock:
            self.gate._sessions.discard(self)

    def fade(self, rate, write, remaining: bytes = b""):
        if self._faded or not self._tail or not self._acquired:
            return
        self._faded = True
        # Fade real unplayed samples when present. At a live-frame boundary
        # only the last sample remains: ramp that value to zero without
        # repeating old waveform cycles (which produces buzz/clicks).
        count = max(1, int(rate * FADE_S))
        tail = np.frombuffer(self._tail, dtype=np.int16)
        if remaining:
            data = np.frombuffer(remaining, dtype=np.int16)[:count].astype(np.float32)
        else:
            if not tail[-1]:
                return
            data = np.full(count, tail[-1], dtype=np.float32)
        data *= np.linspace(1.0, 0.0, len(data), dtype=np.float32)
        stride = max(1, int(rate * FRAME_S))
        data = data.astype(np.int16)
        for start in range(0, len(data), stride):
            write(data[start : start + stride].tobytes())

    def write(self, pcm: bytes, rate: int, write) -> bool:
        if not self._acquired and not self.acquire():
            return False
        stride = max(1, int(rate * FRAME_S)) * 2
        for start in range(0, len(pcm), stride):
            if self.cancelled.is_set():
                self.fade(rate, write, pcm[start:])
                return False
            chunk = pcm[start : start + stride]
            write(chunk)
            self._tail = chunk
        if self.cancelled.is_set():
            self.fade(rate, write)
            return False
        return True


class PlaybackGate:
    def __init__(self):
        self._lock = threading.Lock()
        self._output = threading.Lock()
        self._cancelled = set()
        self._sessions = set()

    def session(self, turn_id=None):
        if turn_id is None:
            turn_id = f"local-{uuid.uuid4().hex}"
        with self._lock:
            session = PlaybackSession(self, turn_id)
            if turn_id in self._cancelled:
                session.cancelled.set()
            self._sessions.add(session)
        return session

    def cancel(self, turn_id):
        with self._lock:
            self._cancelled.add(turn_id)
            for session in self._sessions:
                if session.turn_id == turn_id:
                    session.cancelled.set()

    def cancel_all(self):
        with self._lock:
            for session in self._sessions:
                self._cancelled.add(session.turn_id)
                session.cancelled.set()


_GATE = PlaybackGate()


def get_playback_gate():
    return _GATE
