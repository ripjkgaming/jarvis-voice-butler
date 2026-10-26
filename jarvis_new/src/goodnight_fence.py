"""Shared admission fence and executor leases for the supported Jarvis scope.

No import-time files/threads. Lock ordering: admission before producer stores.
Never hold the admission flock across an await or model request.
"""

from __future__ import annotations

import contextlib
import fcntl
import functools
import os
import stat
import threading
import time
import uuid
from pathlib import Path

_mutex = threading.RLock()
_local = threading.local()


def _after_fork():
    global _mutex, _local
    # A child must not mistake the parent's admission lease for its own.
    for fd in getattr(_local, "held", {}).values():
        os.close(fd)
    _mutex = threading.RLock()
    _local = threading.local()


os.register_at_fork(after_in_child=_after_fork)


def home():
    return Path(os.environ.get("JARVIS_HOME") or Path.home() / ".jarvis")


def _open(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("unsafe goodnight fence directory")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        os.close(fd)
        raise ValueError("unsafe goodnight fence file")
    return fd


@contextlib.contextmanager
def _bounded_mutex():
    deadline = time.monotonic() + 2
    if not _mutex.acquire(timeout=2):
        raise TimeoutError("Goodnight admission fence is busy")
    try:
        yield deadline
    finally:
        _mutex.release()


class Fence:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else home()
        self.directory = self.root / "goodnight-runtime"

    @contextlib.contextmanager
    def __call__(self):
        key = str(self.directory.absolute())
        with _bounded_mutex() as deadline:
            held = getattr(_local, "held", {})
            _local.held = held
            if key in held:
                yield self
                return
            fd = _open(self.directory / "admission.lock")
            try:
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "Goodnight admission fence is busy"
                            ) from None
                        time.sleep(0.005)
                held[key] = fd
                yield self
            finally:
                held.pop(key, None)
                os.close(fd)

    def valid(self):
        return str(self.directory.absolute()) in getattr(_local, "held", {})

    @contextlib.contextmanager
    def work(self):
        # Live FD lease spans asynchronous work + owned transport cleanup.
        with self():
            path = self.directory / "work" / (uuid.uuid4().hex + ".lease")
            fd = _open(path)
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                with self():
                    path.unlink(missing_ok=True)
            finally:
                os.close(fd)

    def work_clear(self):
        directory = self.directory / "work"
        if not directory.exists():
            return True
        if directory.is_symlink():
            return None
        paths = list(directory.iterdir())
        if len(paths) > 4096:
            return None
        try:
            for path in paths:
                # Read existing metadata only: never create missing lease.
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        return None
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        return False
                finally:
                    os.close(fd)
            return True
        except OSError:
            return None


def admission(function):
    """Short synchronous store commit: queued work becomes visible atomically."""

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if os.environ.get("JARVIS_GOODNIGHT_V2") != "1":
            return function(*args, **kwargs)
        with Fence()():
            return function(*args, **kwargs)

    return wrapped


def execution(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        if os.environ.get("JARVIS_GOODNIGHT_V2") != "1":
            return function(*args, **kwargs)
        with Fence().work():
            return function(*args, **kwargs)

    return wrapped


def async_execution(function):
    @functools.wraps(function)
    async def wrapped(*args, **kwargs):
        if os.environ.get("JARVIS_GOODNIGHT_V2") != "1":
            return await function(*args, **kwargs)
        with Fence().work():
            return await function(*args, **kwargs)

    return wrapped
