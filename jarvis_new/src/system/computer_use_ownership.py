"""Exclusive desktop input lease shared by task and one-action tools.

The file lock also covers separate local Jarvis processes. Kernel locks
release on process exit; no stale PID file can permanently lock control.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import functools
import os
from pathlib import Path

from livekit.agents.llm import ToolError


class ControlBusyError(RuntimeError):
    """Another Jarvis operation owns desktop input."""


class ControlLease:
    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)


def acquire_control() -> ControlLease:
    """Take ownership without waiting, so competing tools never queue input."""
    home = Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        home / "computer-use.lock",
        os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ControlBusyError(
            "Another Jarvis action owns desktop input. Wait or cancel computer use."
        ) from None
    except BaseException:
        os.close(fd)
        raise
    return ControlLease(fd)


def serialized_desktop_action(func):
    """Keep one direct input action atomic relative to a computer-use task.

    An input thread cannot be cancelled midway through a key press. Drain the
    already-started operation before releasing ownership, then propagate cancel.
    """

    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        try:
            lease = acquire_control()
        except (ControlBusyError, OSError) as exc:
            raise ToolError(
                "Another Jarvis action owns desktop input, or its lock is unavailable."
            ) from exc
        task = asyncio.create_task(func(*args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Repeated cancel requests must not free the lease early either.
            while not task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(task)
            with contextlib.suppress(BaseException):
                task.result()
            raise
        finally:
            lease.release()

    return wrapped
