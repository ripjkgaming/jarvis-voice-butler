"""Correlate an HTTP voice launch with its first model turn, across threads."""

import threading
from collections.abc import Callable


def external_key(message: object) -> tuple[str, str] | None:
    if not isinstance(message, dict):
        return None
    tid = message.get("turn_id")
    client = message.get("client_id", "default")
    if isinstance(tid, bool) or not isinstance(tid, (int, str)):
        return None
    if not str(tid) or len(str(tid)) > 128 or not isinstance(client, str):
        return None
    return client[:128], str(tid)


class LinkedTurn:
    def __init__(self, key: tuple[str, str]):
        self.key = key
        self.cancelled = threading.Event()
        self.target: int | None = None
        self._armed = False
        self._sender: Callable[[dict], None] | None = None
        self._lock = threading.Lock()

    def bind(self, sender: Callable[[dict], None]) -> None:
        with self._lock:
            self._sender = sender
        if self.cancelled.is_set():
            self.cancel(self.key)

    def seed(self) -> bool:
        with self._lock:
            self._armed = not self.cancelled.is_set()
            return self._armed

    def observe(self, message: dict) -> None:
        tid = message.get("turn_id")
        mapping = (
            message.get("event") == "external_mapping"
            and str(message.get("external_turn_id")) == self.key[1]
            and message.get("client_id") == self.key[0]
        )
        with self._lock:
            if mapping and isinstance(tid, int):
                self.target = tid
            else:
                if not self._armed or self.target is not None:
                    return
                if (
                    message.get("state") != "thinking"
                    or not isinstance(tid, int)
                    or tid <= 0
                ):
                    return
                self.target = tid
        # A cancellation can precede the provider committing the seeded text.
        if self.cancelled.is_set():
            self.cancel(self.key)

    def cancel(self, key: tuple[str, str]) -> bool:
        with self._lock:
            if key != self.key:
                return False
            self.cancelled.set()
            sender, target = self._sender, self.target
        if sender is not None:
            payload = {
                "type": "jarvis_turn",
                "event": "cancel_all",
                "client_id": self.key[0],
                "external_turn_id": self.key[1],
            }
            if target is not None:
                payload.update(event="cancel", turn_id=target)
            sender(payload)
        return True

    def close(self) -> None:
        with self._lock:
            self._sender = None


class ExternalSeedRouter:
    """Agent ingress for typed launches; never also submit them to lk.chat."""

    def __init__(self, binding):
        self.binding = binding
        self.requests: dict[tuple[str, str], str] = {}
        self.targets: dict[tuple[str, str], int] = {}
        self.cancelled: set[tuple[str, str]] = set()
        binding.manager.on_state(lambda _: self._map_pending())

    @staticmethod
    def key(data):
        return external_key(
            {
                "client_id": data.get("client_id", "default"),
                "turn_id": data.get("external_turn_id"),
            }
        )

    def _map_pending(self):
        turn = self.binding.manager.current_turn
        if turn is None:
            return
        for key, text in self.requests.items():
            if (
                key not in self.targets
                and key not in self.cancelled
                and text in turn.text
            ):
                self.targets[key] = turn.id
                self.binding._emit(
                    "external_mapping",
                    turn.id,
                    client_id=key[0],
                    external_turn_id=key[1],
                )

    def handle(self, data: dict) -> bool:
        key = self.key(data)
        event = data.get("event")
        if event == "external_seed":
            text = data.get("text")
            if (
                key is None
                or not isinstance(text, str)
                or not 0 < len(text.strip()) <= 500
            ):
                return True
            if key in self.requests or key in self.cancelled:
                return True
            if len(self.requests) >= 128:
                return True
            self.requests[key] = text.strip()
            binding = self.binding
            session = binding.session
            session._jarvis_wake_request_seen = True
            session._jarvis_greeting_consumed = True
            action = binding.manager.on_user_speech(text.strip())
            self._map_pending()
            # Readiness owns replaying queued boot input.
            if action.turn is None:
                if action.kind == "cancel_all":
                    session.interrupt(force=True)
                return True
            binding.generation += 1
            generation = binding.generation
            binding.restarting = True
            drain = session.interrupt(force=True)

            async def generate():
                import inspect

                if inspect.isawaitable(drain):
                    await drain
                if generation != binding.generation:
                    return
                binding.restarting = False
                if key not in self.cancelled and binding.manager.is_current(
                    action.turn.id
                ):
                    await session.generate_reply(user_input=action.turn.text)

            binding._spawn(generate())
            return True
        if event in {"cancel", "cancel_all"} and key is not None:
            self.cancelled.add(key)
            if key not in self.requests:
                return True  # remember cancellation that beat the seed
            target = self.targets.get(key)
            if target is None:
                self.binding.manager.cancel_all()
            elif self.binding.manager.is_current(target):
                self.binding.manager.cancel(target)
            else:
                return True  # the call has moved to a newer independent request
            self.binding.generation += 1
            self.binding.restarting = False
            self.binding.session.interrupt(force=True)
            return True
        return False
