"""Local session host for ten-minute locking over the registered Jarvis scope.

Only SDK final audio enters intent. No arm RPC, model arm tool, detached lock
watcher, process-name heuristic, or coverage claim for unrelated coding apps.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import math
import os
import re
import uuid

from goodnight_fence import Fence, _open
from goodnight_runtime import Host, SessionLock, WaylandIdle
from goodnight_supported import SupportedInventory
from goodnight_v2 import elapsed_clock

CURRENT = None


def _write(path, value):
    fd = _open(path.with_name(path.name + ".tmp"))
    try:
        os.write(fd, json.dumps(value, allow_nan=False).encode())
        os.ftruncate(fd, os.lseek(fd, 0, os.SEEK_CUR))
    finally:
        os.close(fd)
    os.replace(path.with_name(path.name + ".tmp"), path)


class Conversations:
    def __init__(self, fence, clock=elapsed_clock):
        self.fence, self.clock = fence, clock
        self.path = fence.directory / "conversations" / (uuid.uuid4().hex + ".json")
        self.activity_path = self.path.with_suffix(".activity")

    def activity(self):
        # Tiny atomic content-free signal precedes admission publication. A
        # held final fence must not hide new speech in another voice process.
        _write(self.activity_path, {"at": self.clock()})

    def idle_seconds(self):
        try:
            paths = list(self.path.parent.glob("*.json"))
            if not paths or len(paths) > 4096:
                return None
            now = self.clock()
            ages = []
            for path in paths:
                signal = path.with_suffix(".activity")
                if signal.is_symlink() or signal.stat().st_size > 100:
                    return None
                stamp = json.loads(signal.read_text())["at"]
                if type(stamp) not in {float, int} or not math.isfinite(stamp):
                    return None
                age = now - stamp
                if not math.isfinite(age) or age < 0:
                    return None
                ages.append(age)
            return min(ages)
        except Exception:
            return None

    def update(self, *, agent, user, allowed, epoch=None, closed=False):
        with self.fence():
            if closed:
                self.path.unlink(missing_ok=True)
                self.activity_path.unlink(missing_ok=True)
            else:
                _write(
                    self.path,
                    {
                        "at": self.clock(),
                        "agent": agent,
                        "user": user,
                        "allowed": allowed,
                        "epoch": epoch,
                    },
                )

    def clear(self):
        try:
            paths = list(self.path.parent.glob("*.json"))
            if not paths or len(paths) > 4096:
                return None
            now = self.clock()
            epoch_file = self.fence.directory / "epoch.json"
            epoch = (
                json.loads(epoch_file.read_text())["epoch"]
                if epoch_file.exists()
                else None
            )
            for path in paths:
                if path.is_symlink() or path.stat().st_size > 1024:
                    return None
                row = json.loads(path.read_text())
                if epoch is not None and row.get("epoch") != epoch:
                    return None  # retired but open SDK generation always holds
                stamp = row["at"]
                if type(stamp) not in {float, int} or not math.isfinite(stamp):
                    return None
                age = now - stamp
                if not 0 <= age <= 15 or row["allowed"] is not True:
                    return None
                if row["agent"] not in {"listening", "idle"} or row["user"] not in {
                    "listening",
                    "away",
                }:
                    return False
            return True
        except Exception:
            return None


def control(operation):
    # Voice tools run in the owning process. HTTP bridge uses fixed shared
    # status/cancel only; no body/transcript can create an arm.
    if CURRENT is not None:
        from goodnight_controls import owner_control

        return owner_control(CURRENT.host, operation, owner_allowed=CURRENT.allowed())
    fence = Fence()
    if (fence.directory / "detached" / "capability.json").exists():
        from goodnight_supervisor import client_control

        return client_control(operation)
    try:
        with fence():
            path = fence.directory / "status.json"
            if path.is_symlink() or path.stat().st_size > 2048:
                raise ValueError()
            row = json.loads(path.read_text())
            if not 0 <= elapsed_clock() - row["at"] <= 15:
                raise ValueError()
            epoch = json.loads((fence.directory / "epoch.json").read_text())["epoch"]
            if row.get("epoch") != epoch:
                raise ValueError()
            # A fresh old status file cannot prove its host survived a crash.
            fd = os.open(fence.directory / "host.lock", os.O_RDONLY | os.O_NOFOLLOW)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass  # a live elected host still owns this lease
                else:
                    raise ValueError()
            finally:
                os.close(fd)
            if operation == "cancel":
                _write(fence.directory / "cancel.json", {"host": row["host"]})
                return {"ok": True, "say": "Goodnight cancellation requested."}
            if operation == "status":
                return {
                    "ok": True,
                    "state": row["state"],
                    "reason": row["reason"],
                    "observed_at": row["at"],
                    "say": f"Last reported goodnight lock is {row['state']}: {row['reason']}.",
                }
    except Exception:
        pass
    return {"ok": False, "say": "Goodnight owner host is unavailable."}


class Binding:
    def __init__(self, session, features, *, fence=None, idle=None, session_lock=None):
        self.session, self.features = session, features
        self.fence = fence or Fence()
        self.conversations = Conversations(self.fence)
        self.closed = False
        self.revoked = False
        self.host_id = uuid.uuid4().hex
        self.handlers, self.seen = [], set()
        self.pending_commands = set()
        self.blocks_tools = False
        self.turn_serial = 0
        self.agent_state = str(session.agent_state)
        self.user_state = str(session.user_state)
        self.fd = None
        self.epoch = None
        self.host = Host(
            idle=idle or WaylandIdle(),
            inventory=SupportedInventory(self.fence.root, self.fence),
            session_lock=session_lock
            or SessionLock(os.environ.get("XDG_SESSION_ID", "")),
            owner_allowed=self.allowed,
            conversation_clear=self.conversations.clear,
            conversation_idle=self.conversations.idle_seconds,
            launch_fence=self.fence,
        )
        self.close_task = None
        self.close_event_task = None
        self.task = None
        self.inputs = set()

    def allowed(self):
        try:
            current = json.loads((self.fence.directory / "epoch.json").read_text())[
                "epoch"
            ]
            return (
                current == self.epoch
                and not self.closed
                and not self.revoked
                and self.features.authorized()
                and not self.features.sensitive_active
            )
        except Exception:
            return False

    def armed(self):
        return self.host.controller.status()["state"] == "armed"

    def consume(self, message):
        text = getattr(message, "text_content", "").lower()
        matched = text in self.pending_commands and self.allowed()
        self.pending_commands.discard(text)
        return matched

    async def _thread(self, function, *args, **kwargs):
        worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
            with contextlib.suppress(Exception):
                worker.result()
            raise

    def _publish(self):
        if self.closed:
            return
        self.conversations.update(
            agent=self.agent_state,
            user=self.user_state,
            allowed=self.allowed(),
            epoch=self.epoch,
        )

    def _signal_activity(self):
        try:
            self.conversations.activity()
        except Exception:
            self.revoked = True
            self.host.controller.cancel("conversation evidence unavailable")

    def _schedule_publish(self):
        task = asyncio.create_task(self._thread(self._publish))
        self.inputs.add(task)
        task.add_done_callback(self.inputs.discard)

    def install(self):
        self.epoch = uuid.uuid4().hex
        with self.fence():
            _write(self.fence.directory / "epoch.json", {"epoch": self.epoch})
        self.conversations.activity()
        self._publish()
        self._elect()

        def transcript(event):
            self.host.controller.activity()
            self._signal_activity()
            self._schedule_publish()
            if not self.allowed() or getattr(event, "is_final", False) is not True:
                return
            text = getattr(event, "transcript", "")
            if self.features.is_echo(text):
                return
            key = getattr(event, "item_id", None)
            if key is None and isinstance(
                getattr(event, "created_at", None), (int, float)
            ):
                key = f"at:{event.created_at}"
            if not key or key in self.seen:
                return  # missing trusted event identity never arms
            self.seen.add(key)
            self.turn_serial += 1
            serial = self.turn_serial
            self.pending_commands.clear()
            command = re.fullmatch(
                r"(?:hey jarvis[, ]+)?good ?night(?:[, ]+jarvis)?[.!]?|cancel goodnight(?: schedule)?|goodnight(?: schedule)? status",
                text.lower(),
            )
            self.blocks_tools = bool(command)
            if command:
                self.pending_commands.add(text.lower())
            if len(self.seen) > 1024:
                self.host.close()
                return

            async def dispatch():
                for _ in range(120):
                    if (
                        self.fd is not None
                        or not self.allowed()
                        or serial != self.turn_serial
                    ):
                        break
                    await asyncio.sleep(0.1)
                if self.fd is None or not self.allowed() or serial != self.turn_serial:
                    return
                response = await self._thread(self.host.input, text, direct_owner=True)
                if response and self.allowed():
                    with self.fence.work():
                        speech = self.session.say(response)
                        try:
                            await speech.wait_for_playout()
                        except asyncio.CancelledError:
                            with contextlib.suppress(Exception):
                                speech.interrupt(force=True)
                            drain = asyncio.create_task(speech.wait_for_playout())
                            while not drain.done():
                                try:
                                    await asyncio.shield(drain)
                                except asyncio.CancelledError:
                                    continue
                            with contextlib.suppress(Exception):
                                drain.result()
                            raise

            task = asyncio.create_task(dispatch())
            self.inputs.add(task)
            task.add_done_callback(self.inputs.discard)

        def state(event, kind):
            if kind == "agent":
                self.agent_state = str(getattr(event, "new_state", "unknown"))
            else:
                self.user_state = str(getattr(event, "new_state", "unknown"))
            self.host.controller.activity()
            self._signal_activity()
            self._schedule_publish()

        def close(event):
            self.revoked = True
            self.host.controller.cancel("session closed")
            self._signal_activity()
            if self.close_event_task is None:
                self.close_event_task = asyncio.create_task(self.close())

        for name, callback in [
            ("user_input_transcribed", transcript),
            ("user_state_changed", lambda e: state(e, "user")),
            ("agent_state_changed", lambda e: state(e, "agent")),
            ("close", close),
        ]:
            self.session.on(name, callback)
            self.handlers.append((name, callback))
        self.task = asyncio.create_task(self._poll())
        return self

    def _elect(self):
        global CURRENT
        if self.fd is not None or not self.allowed():
            return
        fd = _open(self.fence.directory / "host.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return
        self.fd = fd
        CURRENT = self

    def _release_host(self):
        global CURRENT
        try:
            with self.fence():
                status = self.fence.directory / "status.json"
                if (
                    status.exists()
                    and json.loads(status.read_text()).get("host") == self.host_id
                ):
                    status.unlink()
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if CURRENT is self:
                CURRENT = None

    def _step(self):
        if self.closed:
            return
        self._publish()
        if self.fd is not None and not self.allowed():
            try:
                self.host.close()
            finally:
                self._release_host()
        self._elect()
        if self.fd is None:
            return
        cancel = self.fence.directory / "cancel.json"
        with self.fence():
            queued = cancel.exists()
            requested = (
                queued and json.loads(cancel.read_text()).get("host") == self.host_id
            )
            if queued:
                cancel.unlink()
        if requested:
            self.host.controller.cancel("cancelled")
        state = self.host.tick()
        with self.fence():
            _write(
                self.fence.directory / "status.json",
                {
                    "host": self.host_id,
                    "epoch": self.epoch,
                    "at": elapsed_clock(),
                    "state": state["state"],
                    "reason": state["reason"],
                },
            )

    async def _poll(self):
        while not self.closed:
            try:
                await self._thread(self._step)
            except Exception:
                self.host.controller.cancel("host evidence unavailable")
            await asyncio.sleep(5)

    async def close(self):
        self.revoked = True
        self.host.controller.cancel("session closed")
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)

    async def _close(self):
        if self.closed:
            return
        self.closed = True
        try:
            for name, callback in self.handlers:
                with contextlib.suppress(Exception):
                    self.session.off(name, callback)
            self.host.controller.cancel("session closed")
            for task in self.inputs:
                task.cancel()
            await asyncio.gather(*self.inputs, return_exceptions=True)
            if self.task is not None and self.task is not asyncio.current_task():
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            await self._thread(self.host.close)
            await self._thread(
                self.conversations.update,
                agent=None,
                user=None,
                allowed=False,
                closed=True,
            )
        finally:
            await self._thread(self._release_host)


def install(session, features):
    if os.environ.get("JARVIS_GOODNIGHT_V2") != "1":
        return None
    from goodnight_detached import Binding as DetachedBinding

    return DetachedBinding(session, features).install()
