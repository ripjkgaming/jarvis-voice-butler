"""Single game at a time: ``~/.jarvis/games/chess.lock`` (flock + pid)."""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path

from games.chess.settings import lock_path


class GameLock:
    def __init__(self, path: Path | None = None):
        self.path = path or lock_path()
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")  # noqa: SIM115 - held for the process lifetime
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh
        return True

    def holder_pid(self) -> int | None:
        try:
            return int(self.path.read_text().strip() or 0) or None
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is not None:
            try:
                fcntl.flock(fh, fcntl.LOCK_UN)
            finally:
                fh.close()
            with contextlib.suppress(OSError):
                self.path.unlink()
