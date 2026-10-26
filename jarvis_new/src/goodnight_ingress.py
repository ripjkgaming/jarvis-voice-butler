"""Trusted SDK ingress with revocation, metadata dedup and conversation facts.

Verified against installed livekit-agents 1.8.2 events.py/agent_session.py/
speech_handle.py. AgentSession.say synchronously schedules a SpeechHandle;
add_done_callback tracks its completion without an unawaited coroutine.
No generic history, LLM/tool argument or transcript-derived owner authority.
"""

from __future__ import annotations

import contextlib
import inspect
import math
import threading
import time
import uuid

AGENT_STATES = {"initializing", "idle", "listening", "thinking", "speaking"}
USER_STATES = {"listening", "speaking", "away"}
MAX_SEEN = 2048
MAX_EVENT_AGE_S = 30.0
MAX_FUTURE_S = 5.0


def elapsed_clock():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class IngressBinding:
    """Callable shutdown/unbind handle; state snapshot is content-free.

    conversation_update is a trusted synchronous observer. It must atomically
    publish epoch/revision facts under the coordinator's producer/lock fence.
    This local state alone does not inventory other sessions, calls or tools.
    """

    def __init__(
        self,
        session,
        host,
        *,
        source_is_owner,
        conversation_update=None,
        clock=elapsed_clock,
        wall_clock=time.time,
    ):
        self.session, self.host = session, host
        self.source_is_owner = source_is_owner
        self.conversation_update = conversation_update
        self.clock, self.wall_clock = clock, wall_clock
        self.max_seen = MAX_SEEN
        self._mutex = threading.RLock()
        self._closed = False
        self._epoch = uuid.uuid4().hex
        self._revision = 0
        self._observed_at = clock()
        self._agent = self._state(getattr(session, "agent_state", None), AGENT_STATES)
        self._user = self._state(getattr(session, "user_state", None), USER_STATES)
        self._pending = set()
        self._speech_callbacks = {}
        self._seen = set()
        self._handlers = []
        host.controller.cancel("new owner voice session")
        for event, callback in [
            ("user_input_transcribed", self.transcript),
            ("user_state_changed", self.user_state),
            ("agent_state_changed", self.agent_state),
            ("close", self.close_event),
        ]:
            session.on(event, callback)
            self._handlers.append((event, callback))
        if self._authority():
            self._publish()

    @staticmethod
    def _state(value, choices):
        return value if isinstance(value, str) and value in choices else None

    def snapshot(self):
        with self._mutex:
            return {
                "epoch": self._epoch,
                "revision": self._revision,
                "observed_at": self._observed_at,
                "closed": self._closed,
                "agent_state": self._agent,
                "user_state": self._user,
                "pending_speech": len(self._pending),
            }

    def conversation_clear(self):
        if not self._authority():
            return False
        state = self.snapshot()
        if state["agent_state"] is None or state["user_state"] is None:
            return None
        return (
            not state["closed"]
            and state["agent_state"] in {"idle", "listening"}
            and state["user_state"] in {"listening", "away"}
            and state["pending_speech"] == 0
        )

    def _authority(self):
        with self._mutex:
            if self._closed:
                return False
        try:
            allowed = self.source_is_owner() is True
        except Exception:
            allowed = False
        if not allowed:
            self._revoke()
        return allowed

    def _changed(self):
        with self._mutex:
            self._revision += 1
            self._observed_at = self.clock()

    def _publish(self):
        if self.conversation_update is None:
            return
        try:
            result = self.conversation_update(self.snapshot())
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("conversation observer must be synchronous")
        except Exception:
            self._revoke(publish=False)

    def _revoke(self, *, publish=True):
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
            speech_callbacks, self._speech_callbacks = self._speech_callbacks, {}
            self._changed()
            handlers, self._handlers = self._handlers, []
        # Revocation precedes observer/unsubscription failures.
        self.host.close()
        for speech, callback in speech_callbacks.values():
            remove = getattr(speech, "remove_done_callback", None)
            if callable(remove):
                with contextlib.suppress(Exception):
                    remove(callback)
        for event, callback in handlers:
            with contextlib.suppress(Exception):
                self.session.off(event, callback)
        if publish:
            self._publish()

    def __call__(self):
        """Shutdown/unbind revokes the arm, never merely extends idle time."""
        self._revoke()

    def close_event(self, event):
        self._revoke()

    def _new_final(self, event):
        item_id = getattr(event, "item_id", None)
        created = getattr(event, "created_at", None)
        if item_id is not None:
            if not isinstance(item_id, str) or not 0 < len(item_id) <= 256:
                self._revoke()
                return False
            key = ("item", item_id)
        elif created is not None:
            key = ("created", created)
        else:
            key = ("object", id(event))
        try:
            with self._mutex:
                if key in self._seen:
                    return False
            if created is not None and (
                type(created) not in {float, int}
                or not math.isfinite(created)
                or not -MAX_FUTURE_S <= self.wall_clock() - created <= MAX_EVENT_AGE_S
            ):
                self._revoke()
                return False
            with self._mutex:
                overflow = len(self._seen) >= self.max_seen
                if not overflow:
                    self._seen.add(key)
            if overflow:
                self._revoke()
                return False
            return True
        except (TypeError, ValueError):
            self._revoke()
            return False

    def transcript(self, event):
        if not self._authority():
            return
        final = getattr(event, "is_final", False) is True
        if final and not self._new_final(event):
            return
        self.host.controller.activity()
        self._changed()
        self._publish()
        if not final or not self._authority():
            return
        try:
            response = self.host.input(
                getattr(event, "transcript", ""), direct_owner=True
            )
        except Exception:
            self._revoke()
            return
        if response and self._authority():
            self._say(response)

    def user_state(self, event):
        if not self._authority():
            return
        with self._mutex:
            self._user = self._state(getattr(event, "new_state", None), USER_STATES)
        self.host.controller.activity()
        self._changed()
        self._publish()

    def agent_state(self, event):
        if not self._authority():
            return
        with self._mutex:
            self._agent = self._state(getattr(event, "new_state", None), AGENT_STATES)
        self.host.controller.activity()
        self._changed()
        self._publish()

    def _say(self, response):
        marker = uuid.uuid4().hex
        with self._mutex:
            self._pending.add(marker)
        self._changed()
        self._publish()
        if not self._authority():
            return
        try:
            speech = self.session.say(response)
            # A coroutine would indicate an unsupported SDK/provider contract.
            # SpeechHandle itself is awaitable but already scheduled synchronously.
            if inspect.iscoroutine(speech):
                speech.close()
                raise TypeError("unexpected asynchronous say contract")
            if not callable(getattr(speech, "done", None)) or not callable(
                getattr(speech, "add_done_callback", None)
            ):
                raise TypeError("speech completion unavailable")

            def completed(handle):
                if not self._authority():
                    return
                with self._mutex:
                    self._pending.discard(marker)
                    self._speech_callbacks.pop(marker, None)
                self.host.controller.activity()
                self._changed()
                self._publish()

            with self._mutex:
                if self._closed:
                    return
                self._speech_callbacks[marker] = (speech, completed)
            speech.add_done_callback(completed)
        except Exception:
            self._revoke()


def install(
    session,
    host,
    *,
    source_is_owner,
    conversation_update=None,
    clock=elapsed_clock,
    wall_clock=time.time,
):
    return IngressBinding(
        session,
        host,
        source_is_owner=source_is_owner,
        conversation_update=conversation_update,
        clock=clock,
        wall_clock=wall_clock,
    )
