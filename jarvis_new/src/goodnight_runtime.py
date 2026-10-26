"""Reviewed host integration primitives; import never starts or arms anything."""

from __future__ import annotations

import math
import os
import select
import subprocess
import threading
import time
from pathlib import Path

from goodnight_v2 import Controller, Evidence, IntentGate, elapsed_clock


class WaylandIdle:
    def __init__(self, *, spawn=subprocess.Popen):
        self.spawn, self.process = spawn, None
        self.sequence = 0
        self.mutex = threading.Lock()

    def start(self):
        # Explicit host action only, never inspection/import/status. No shell.
        with self.mutex:
            if self.process is None:
                self.process = self.spawn(
                    [
                        "/usr/bin/python3",
                        str(Path(__file__).with_name("goodnight_idle_helper.py")),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )

    def read(self):
        with self.mutex:
            p = self.process
            if p is None or p.poll() is not None:
                return None
            try:
                self.sequence += 1
                p.stdin.write(f"{self.sequence}\n")
                p.stdin.flush()
                if not select.select([p.stdout], [], [], 2)[0]:
                    self._close()
                    return None
                fields = p.stdout.readline(100).split()
                if len(fields) != 2 or int(fields[0]) != self.sequence:
                    raise ValueError("invalid idle response")
                value = float(fields[1])
                return value if math.isfinite(value) and value >= 0 else None
            except Exception:
                self._close()
                return None

    def _close(self):
        p, self.process = self.process, None
        if p is not None:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)
            p.stdin.close()
            p.stdout.close()

    def close(self):
        with self.mutex:
            self._close()


class SessionLock:
    """Validate explicitly configured local owner Wayland session; verified receipt.

    Never lock all sessions. JARVIS_LOCAL=1 required by the established flow.
    run is injectable; synthetic tests never run the default subprocess callback.
    """

    def __init__(self, session_id, *, run=subprocess.run):
        self.session_id, self.run = session_id, run

    def _run(self, args):
        return self.run(args, capture_output=True, text=True, timeout=3, check=False)

    def identity(self):
        try:
            if (
                os.environ.get("JARVIS_LOCAL") != "1"
                or not self.session_id
                or not self.session_id.isalnum()
                or os.environ.get("XDG_SESSION_ID") != self.session_id
                or not os.environ.get("WAYLAND_DISPLAY")
            ):
                return None
            result = self._run(
                [
                    "loginctl",
                    "show-session",
                    self.session_id,
                    "-p",
                    "User",
                    "-p",
                    "Type",
                    "-p",
                    "Active",
                    "-p",
                    "Remote",
                    "-p",
                    "LockedHint",
                ]
            )
            data = dict(line.split("=", 1) for line in result.stdout.splitlines())
            if (
                result.returncode
                or data.get("User") != str(os.getuid())
                or data.get("Type") != "wayland"
                or data.get("Active") != "yes"
                or data.get("Remote") != "no"
                or data.get("LockedHint") != "no"
            ):
                return None
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return f"{boot}/{self.session_id}/{os.environ.get('WAYLAND_DISPLAY', '')}"
        except Exception:
            return None

    def prepare(self, expected):
        if self.identity() != expected:
            return None
        return self._request

    def lock(self, expected):
        request = self.prepare(expected)
        return request() if request is not None else False

    def _request(self):
        result = self._run(["loginctl", "lock-session", self.session_id])
        if result.returncode:
            return False
        # KDE ScreenSaver GetActive is read-back, not the request's exit code.
        for _ in range(5):
            readback = self._run(
                [
                    "busctl",
                    "--user",
                    "call",
                    "org.freedesktop.ScreenSaver",
                    "/ScreenSaver",
                    "org.freedesktop.ScreenSaver",
                    "GetActive",
                ]
            )
            if readback.returncode == 0 and readback.stdout.strip() == "b true":
                return True
            time.sleep(0.1)
        return False


class Host:
    """Single elected desktop owner host; external coverage MUST be authoritative.

    conversation_clear must include all owner conversations/calls, freshness,
    tool execution and revoked/guest authority. None or missing provider holds.
    Host process lifetime owns the arm; close cancels. No persistent arm reload.
    """

    def __init__(
        self,
        *,
        idle,
        inventory,
        session_lock,
        owner_allowed,
        conversation_clear,
        conversation_idle=None,
        launch_fence=None,
        clock=elapsed_clock,
    ):
        self.idle, self.inventory, self.session_lock = idle, inventory, session_lock
        self.owner_allowed, self.conversation_clear = owner_allowed, conversation_clear
        self.launch_fence = launch_fence
        self.conversation_idle = conversation_idle
        self.controller = Controller(
            probe=self.probe,
            lock=lambda expected: self.session_lock.lock(expected),
            clock=clock,
            finalizer=self._fenced_lock,
        )
        self.gate = IntentGate(self.controller)

    def probe(self):
        session = self.session_lock.identity()
        idle = self.idle.read()
        tasks = self.inventory.clear() if self.launch_fence else None
        conversation = self.conversation_clear()
        owner = self.owner_allowed()
        if self.conversation_idle is not None:
            quiet = self.conversation_idle()  # last read after slow probes
            idle = (
                min(idle, quiet)
                if all(
                    type(v) in {float, int} and math.isfinite(v) and v >= 0
                    for v in (idle, quiet)
                )
                else None
            )
        return Evidence(idle, tasks, conversation, owner, session)

    def _fenced_lock(self, expected, commit):
        if self.launch_fence is None:
            return None
        # Fence acquisition precedes the controller commit mutex; cancellation
        # stays responsive while admission is busy. Commit rechecks expiry,
        # token and continuity after the fresh final probe under this fence.
        with self.launch_fence() as lease:
            if lease.valid() is not True:
                return None
            return commit(self.probe(), lambda: self.session_lock.lock(expected))

    def input(self, text, *, direct_owner):
        # Direct source flag belongs to authenticated ingress, never a tool arg.
        result = self.gate.input(
            text,
            owner=direct_owner and self.owner_allowed() is True,
            direct=direct_owner,
            session=self.session_lock.identity(),
        )
        if self.controller.status()["state"] == "armed":
            self.idle.start()
        return result

    def tick(self):
        state = self.controller.tick()
        if state["state"] != "armed":
            self.idle.close()
        return state

    def close(self):
        self.controller.cancel("host closed")
        self.idle.close()
