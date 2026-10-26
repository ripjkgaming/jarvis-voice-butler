"""Private native tray -> screen host transport. No HTTP or model command API."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import socket
import stat
import struct
from pathlib import Path

from screen_session_holder import LocalControlEvent, PhysicalScreenControls


class NativePeerVerifier:
    """Pin approved native executable metadata; verify Linux kernel credentials.

    The executable path is explicit host configuration, not client input. Only
    native tray handlers call this transport; no webview/CLI command exports it.
    Trust is the owner's native executable/OS account, not protection against a
    compromised owner account or hostile code already inside that executable.
    """

    def __init__(self, executable, *, inspect=os.stat, uid=None):
        path = Path(executable)
        if not path.is_absolute():
            raise ValueError("Explicit absolute native executable is required")
        self.uid = os.getuid() if uid is None else uid
        self.inspect = inspect
        pinned = (
            inspect(path, follow_symlinks=False)
            if inspect is os.stat
            else inspect(path)
        )
        if (
            not stat.S_ISREG(pinned.st_mode)
            or pinned.st_uid != self.uid
            or pinned.st_mode & 0o022
            or not pinned.st_mode & 0o111
        ):
            raise ValueError("Private owner native executable is required")
        self.identity = (pinned.st_dev, pinned.st_ino)

    def allows(self, peer):
        try:
            pid, uid, _ = struct.unpack(
                "3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if uid != self.uid or pid <= 0:
                return False
            current = self.inspect(Path(f"/proc/{pid}/exe"))
            return (
                current.st_uid == self.uid
                and (current.st_dev, current.st_ino) == self.identity
            )
        except (OSError, ValueError, AttributeError, TypeError):
            return False


class NativeControlServer:
    def __init__(self, path, holder, verifier, *, on_resume=lambda: None):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ValueError("Explicit absolute control socket is required")
        self.holder = holder
        self.verifier = verifier
        self.on_resume = on_resume
        self._authenticated = set()
        self.controls = PhysicalScreenControls(
            holder, authorize_event=lambda event: event in self._authenticated
        )
        self._server = None
        self._identity = None
        self._tasks = set()
        self._closed = False

    async def start(self):
        parent = self.path.parent
        info = parent.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or parent.resolve() != parent
        ):
            raise ValueError(
                "Control socket requires an existing private owner directory"
            )
        try:
            self.path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise ValueError(
                "Control path already exists; no existing socket is removed"
            )
        self._server = await asyncio.start_unix_server(
            self._connected, path=str(self.path), limit=128
        )
        info = self.path.lstat()
        self._identity = (info.st_dev, info.st_ino)

    def _connected(self, reader, writer):
        task = asyncio.create_task(self.handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(
            lambda done: None if done.cancelled() else done.exception()
        )

    async def handle(self, reader, writer):
        event = None
        try:
            peer = writer.get_extra_info("socket")
            if self._closed or not self.verifier.allows(peer):
                raise ValueError("Native authentication refused")
            raw = await asyncio.wait_for(reader.readuntil(b"\n"), 5)
            if len(raw) > 32 or raw not in {b"stop\n", b"resume_quiet\n", b"hangup\n"}:
                raise ValueError("Unsupported native control")
            # Native client half-closes its write side: exactly one command.
            if await asyncio.wait_for(reader.read(1), 5):
                raise ValueError("Multiple commands refused")
            if not self.verifier.allows(peer):
                raise ValueError("Native identity changed")
            action = {
                b"stop\n": "stop",
                b"resume_quiet\n": "resume",
                b"hangup\n": "hangup",
            }[raw]
            event = LocalControlEvent(
                secrets.token_hex(16),
                "kernel-verified-native",
                action,
                quiet_confirmed=action == "resume",
                received_at=self.holder.clock(),
            )
            self._authenticated.add(event)
            result = await self.controls.dispatch(event)
            if action == "resume":
                try:
                    self.on_resume()  # explicit host consent loader, never payload consent
                except Exception:
                    await self.holder.stop()
                    raise
            status = result["status"]
            if status not in {"stopped", "listening", "closed"}:
                raise ValueError("Unrecognized lifecycle result")
            writer.write(f"ok {status} {result.get('generation', 0)}\n".encode("ascii"))
            await writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            with contextlib.suppress(Exception):
                writer.write(b"error refused\n")
                await writer.drain()
        finally:
            if event is not None:
                self._authenticated.discard(event)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def aclose(self):
        self._closed = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._identity is not None:
            with contextlib.suppress(FileNotFoundError):
                info = self.path.lstat()
                if (info.st_dev, info.st_ino) == self._identity:
                    self.path.unlink()
