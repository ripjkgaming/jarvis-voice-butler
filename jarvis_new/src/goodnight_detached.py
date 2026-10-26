"""Trusted SDK ingress/coverage; plan is owned by bridge after acknowledged arm.

Closing this binding drains owned speech and publishes settled conversation.
It never closes the supervisor, cancels an accepted arm, or keeps SDK input alive.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import uuid

from goodnight_supervisor import Calls, Client, owner_allowed, revoke_security

from goodnight_fence import Fence
from goodnight_host import _write
from goodnight_v2 import elapsed_clock


class Binding:
    def __init__(
        self,
        session,
        features,
        *,
        fence=None,
        client=None,
        clock=elapsed_clock,
        owner=owner_allowed,
    ):
        self.session, self.features = session, features
        self.owner = owner
        self.close_event_task = None
        self.fence, self.clock = fence or Fence(), clock
        self.calls = Calls(self.fence, clock)
        self.call_id = uuid.uuid4().hex
        self.path = self.calls.directory / (self.call_id + ".json")
        self.activity_path = self.path.with_suffix(".activity")
        self.client = client
        self.agent_state, self.user_state = (
            str(session.agent_state),
            str(session.user_state),
        )
        self.closed = self.closing = False
        self.seen = set()
        self.pending_commands = set()
        self.blocks_tools = False
        self.inputs = set()
        self.handlers = []
        self.poll_task = self.close_task = None
        self.serial = 0

    def allowed(self):
        return (
            not self.closing
            and not self.closed
            and self.features.authorized()
            and not self.features.sensitive_active
        )

    def armed(self):
        # Ordinary idle hangup/presence behavior may end this conversation.
        # The supervisor keeps the plan; no microphone/session lease is needed.
        return False

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

    def _activity(self):
        if not self.closing and self.owner() is not True:
            revoke_security()
        try:
            _write(self.activity_path, {"at": self.clock()})
        except Exception:
            revoke_security()  # durable cross-host fail-closed invalidation
            raise

    def _publish(self):
        if not self.closing and self.owner() is not True:
            revoke_security()
        with self.fence():
            _write(
                self.path,
                {
                    "at": self.clock(),
                    "agent": self.agent_state,
                    "user": self.user_state,
                    "allowed": self.allowed(),
                },
            )

    def _schedule(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.inputs.add(task)
        task.add_done_callback(self.inputs.discard)

    def install(self):
        self._activity()
        self._publish()  # register before provider/input starts
        if self.client is None:
            try:
                self.client = Client(self.fence)
            except Exception:
                self.client = None  # no accepted plan; coverage still holds

        def transcript(event):
            if self.closing:
                return
            self._activity()
            self._schedule(self._thread(self._publish))
            if not self.allowed() or getattr(event, "is_final", False) is not True:
                return
            text = getattr(event, "transcript", "")
            if self.features.is_echo(text):
                return
            identity = getattr(event, "item_id", None)
            if identity is None and type(getattr(event, "created_at", None)) in {
                float,
                int,
            }:
                identity = f"at:{event.created_at}"
            if not identity or identity in self.seen:
                return
            if len(self.seen) >= 1024:
                revoke_security()
                return
            self.seen.add(identity)
            self.serial += 1
            serial = self.serial
            self.pending_commands.clear()
            normalized = text.lower()
            arm = re.fullmatch(
                r"(?:hey jarvis[, ]+)?good ?night(?:[, ]+jarvis)?[.!]?", normalized
            )
            operation = (
                "arm"
                if arm
                else {
                    "cancel goodnight": "cancel",
                    "cancel goodnight schedule": "cancel",
                    "goodnight status": "status",
                    "goodnight schedule status": "status",
                }.get(normalized)
            )
            self.blocks_tools = bool(operation)
            if not operation or len(self.seen) > 1024:
                return
            self.pending_commands.add(normalized)
            turn = hashlib.sha256(str(identity).encode()).hexdigest()
            self._schedule(
                self._dispatch(operation, turn, serial, self.clock(), uuid.uuid4().hex)
            )

        def state(event, kind):
            if self.closing:
                return
            setattr(self, kind + "_state", str(getattr(event, "new_state", "unknown")))
            self._activity()
            self._schedule(self._thread(self._publish))

        def close(event):
            # Session shutdown is settlement, not authority revocation.
            self.closing = True
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
        self.poll_task = asyncio.create_task(self._poll())
        return self

    async def _dispatch(self, operation, turn, serial, issued, generation):
        if not self.allowed() or serial != self.serial:
            return
        options = {
            "call": self.call_id,
            "turn": turn,
            "issued": issued,
            "generation": generation,
        }
        # Authorization is rechecked at issuance; bridge independently validates
        # signed owner generation and current authority under final admission.
        if not self.allowed() or serial != self.serial:
            return
        if self.client is None:
            result = {"ok": False}
        elif operation == "cancel":
            result = await self._thread(self.client.cancel, **options)
        else:
            result = await self._thread(self.client.send, operation, **options)
        if not self.allowed():
            return
        state = result.get("current", {})
        if operation == "arm":
            answer = (
                "Goodnight, Sir. The supervisor will lock after ten idle minutes once registered work finishes, even after this conversation ends. The request expires in one hour."
                if result.get("ok") and state.get("state") == "armed"
                else "Goodnight handoff was not confirmed. Please check status or cancel; I cannot promise a pending lock."
            )
        elif operation == "cancel":
            answer = (
                "Goodnight lock cancelled."
                if result.get("ok")
                else "Cancellation unavailable or the lock request already started."
            )
        else:
            answer = f"Goodnight lock is {state.get('state', 'unavailable')}: {state.get('reason', 'unknown')}."
        with self.fence.work():
            speech = self.session.say(answer)
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

    async def _poll(self):
        while not self.closing:
            try:
                await self._thread(self._publish)
            except Exception:
                revoke_security()
            await asyncio.sleep(5)

    async def close(self):
        self.closing = True
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._close())
        await asyncio.shield(self.close_task)

    async def _close(self):
        if self.closed:
            return
        self.closed = True
        for name, callback in self.handlers:
            with contextlib.suppress(Exception):
                self.session.off(name, callback)
        # Do not cancel the close event's caller or await its own close cycle.
        tasks = [t for t in self.inputs if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if self.poll_task is not None:
            self.poll_task.cancel()
            tasks.append(self.poll_task)
        await asyncio.gather(*tasks, return_exceptions=True)

        def settle():
            with self.fence():
                _write(self.activity_path, {"at": self.clock()})
                _write(
                    self.path,
                    {
                        "at": self.clock(),
                        "agent": "closed",
                        "user": "closed",
                        "allowed": False,
                        "closed": True,
                    },
                )

        await self._thread(settle)
