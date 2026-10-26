"""Owned, bounded sampling of explicitly registered window observations only.

No discovery, notifications, input, launch, process signals or build-success
claims. Starts only after an owner-requested activity registration; existing
background-monitor flags are unchanged. No persistent autostart on reboot.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import threading

import build_monitor as bm
import jobs
import window_monitor as wm

_lock = threading.RLock()
_workers = {}


class Sampler:
    def __init__(self, monitor, *, resolver=None, allowed=None, interval=2.0):
        if type(interval) not in {int, float} or not 2 <= interval <= 60:
            raise ValueError("Sampling interval must be 2-60 seconds.")
        self.monitor = monitor
        self.resolver = resolver or wm.Resolver(monitor)
        self.allowed = allowed or (
            lambda: bm.tools_enabled() and bm.owner_allowed("127.0.0.1")
        )
        self.interval = interval
        self.stop = threading.Event()
        self.retiring = threading.Event()

    def check(self):
        if self.stop.is_set() or not self.allowed():
            raise wm.WindowError("Window observation authority unavailable.", 403)

    def tick(self):
        self.check()
        # Registry enforces one active window observation; bounded reads also
        # tolerate synthetic/corrupt multiples without unbounded metadata work.
        with _lock:
            self.check()
            rows = self.monitor.registry.list(wm.OWNER)
            active = [r for r in rows if r["lifecycle"] != "settled"]
            if len(active) > 1:
                self.retiring.set()
                raise wm.WindowError("Multiple active window observations refused.")
            if not active:
                # Atomic with ensure_started's reuse decision in this process.
                self.retiring.set()
                return False
        for row in active:
            self.resolver.status(row["id"], check=self.check)
        return bool(active)

    def run(self):
        while not self.stop.is_set():
            try:
                if not self.tick():
                    return
            except (jobs.JobError, OSError, ValueError):
                # Transport failures become unknown through Resolver.status.
                # Unsafe stores or authority failures stop rather than polling
                # undisclosed window metadata after guest/private transitions.
                self.retiring.set()
                return
            self.stop.wait(self.interval)
        self.retiring.set()


def ensure_started(monitor):
    """Elect one daemon sampler; no other service/process is started.

    True means this process started or reused a non-retiring elected observer.
    Other-process lock contention is reported unavailable, not active. A thread
    start does not prove a sample succeeded or an announcement was delivered.
    """
    key = str(monitor.registry.root)
    with _lock:
        previous = _workers.get(key)
        if previous and previous[0].is_alive():
            if not previous[1].stop.is_set() and not previous[1].retiring.is_set():
                return True
            # Closing thread's finally only closes its FD, never takes _lock.
            # Wait a bounded interval before attempting a replacement election.
            if previous[0] is threading.current_thread():
                return False
            previous[0].join(4.0)
            if previous[0].is_alive():
                return False
        sampler = Sampler(monitor)
        sampler.check()
        fd = os.open(
            monitor.registry.root / "window-monitor.owner.lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
            ):
                raise wm.WindowError("Unsafe window sampler lock.")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                # A held FD is not proof of an active other-process observer.
                return False

            def work():
                try:
                    sampler.run()
                finally:
                    sampler.retiring.set()
                    os.close(fd)

            thread = threading.Thread(target=work, name="window-observer", daemon=True)
            thread.start()
            _workers[key] = (thread, sampler)
            return True
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise


def stop_owned(monitor, timeout=4.0):
    """Drain only this process's sampler; never signal the observed process."""
    with _lock:
        pair = _workers.get(str(monitor.registry.root))
    if not pair:
        return True
    thread, sampler = pair
    sampler.stop.set()
    if thread is threading.current_thread():
        return False
    thread.join(timeout)
    stopped = not thread.is_alive()
    if stopped:
        with _lock:
            if _workers.get(str(monitor.registry.root)) is pair:
                _workers.pop(str(monitor.registry.root), None)
    return stopped
