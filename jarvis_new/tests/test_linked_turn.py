import threading

from linked_turn import LinkedTurn, external_key
from wake_client import WakeClient


def test_external_key_legacy_and_validation():
    assert external_key({"talk": True}) is None
    assert external_key({"turn_id": True}) is None
    assert external_key({"turn_id": 42, "client_id": "phone"}) == ("phone", "42")


def test_cancel_after_launch_targets_only_original_turn():
    messages = []
    link = LinkedTurn(("phone", "42"))
    link.bind(messages.append)
    assert link.seed()
    link.observe({"state": "speaking", "turn_id": 0})  # boot greeting
    link.observe({"state": "thinking", "turn_id": 100})
    link.observe({"state": "thinking", "turn_id": 101})  # unrelated later request
    assert link.cancel(("other", "42")) is False
    assert messages == []
    assert link.cancel(("phone", "42"))
    assert messages == [
        {
            "type": "jarvis_turn",
            "event": "cancel",
            "turn_id": 100,
            "client_id": "phone",
            "external_turn_id": "42",
        }
    ]


def test_cancel_before_model_commits_seed_is_reissued_with_actual_id():
    messages = []
    link = LinkedTurn(("phone", "42"))
    link.bind(messages.append)
    assert link.seed()
    link.cancel(link.key)
    assert messages[-1]["event"] == "cancel_all"
    link.observe({"state": "thinking", "turn_id": 100})
    assert messages[-1] == {
        "type": "jarvis_turn",
        "event": "cancel",
        "turn_id": 100,
        "client_id": "phone",
        "external_turn_id": "42",
    }


def test_cancel_before_connected_prevents_seed_and_closed_room_is_not_called():
    link = LinkedTurn(("phone", "42"))
    assert link.cancel(link.key)
    assert link.seed() is False
    messages = []
    link.bind(messages.append)
    assert messages[-1]["event"] == "cancel_all"
    link.close()
    link.cancel(link.key)
    assert len(messages) == 1


def test_pending_external_cancel_drops_seed_and_summon_event():
    client = WakeClient.__new__(WakeClient)
    client._mic_lock = threading.Lock()
    client._talk_event = threading.Event()
    client._talk_event.set()
    client._pending_text = "send mail"
    client._pending_linked_turn = LinkedTurn(("phone", "42"))
    assert client._cancel_linked_turn({"turn_id": 42, "client_id": "other"}) is False
    assert client._talk_event.is_set()
    assert client._cancel_linked_turn({"turn_id": 42, "client_id": "phone"})
    assert client._pending_text == ""
    assert not client._talk_event.is_set()
    assert client._pending_linked_turn.cancelled.is_set()


async def test_agent_typed_seed_deduplicates_and_external_cancel_preserves_new_turn():
    import asyncio
    from types import SimpleNamespace

    from linked_turn import ExternalSeedRouter
    from turn_manager import VoiceTurnBinding

    class Session:
        def __init__(self):
            self.replies = []
            self.interrupts = 0

        def on(self, *_):
            pass

        def interrupt(self, **_):
            self.interrupts += 1
            future = asyncio.get_running_loop().create_future()
            future.set_result(None)
            return future

        async def generate_reply(self, **kwargs):
            self.replies.append(kwargs["user_input"])

    messages = []
    session = Session()
    binding = VoiceTurnBinding(
        session, SimpleNamespace(is_echo=lambda _: False), messages.append
    )
    binding.ready()
    router = ExternalSeedRouter(binding)
    seed = {
        "type": "jarvis_turn",
        "event": "external_seed",
        "client_id": "phone",
        "external_turn_id": "42",
        "text": "send once",
    }
    assert router.handle(seed)
    assert router.handle(seed)
    await asyncio.sleep(0)
    assert session.replies == ["send once"]
    target = binding.manager.current_turn.id
    assert any(
        m["event"] == "external_mapping" and m["turn_id"] == target for m in messages
    )
    binding.manager.on_user_speech("a different request")
    newer = binding.manager.current_turn
    before = session.interrupts
    router.handle({**seed, "event": "cancel_all"})
    assert not newer.cancelled
    assert session.interrupts == before
    await binding.close()


async def test_agent_cancel_arriving_before_seed_prevents_generation():
    import asyncio
    from types import SimpleNamespace

    from linked_turn import ExternalSeedRouter
    from turn_manager import VoiceTurnBinding

    class Session:
        def __init__(self):
            self.replies = []

        def on(self, *_):
            pass

        def interrupt(self, **_):
            pass

        async def generate_reply(self, **kwargs):
            self.replies.append(kwargs)

    session = Session()
    binding = VoiceTurnBinding(session, SimpleNamespace(is_echo=lambda _: False))
    binding.ready()
    router = ExternalSeedRouter(binding)
    base = {"type": "jarvis_turn", "client_id": "phone", "external_turn_id": "42"}
    router.handle({**base, "event": "cancel_all"})
    router.handle({**base, "event": "external_seed", "text": "must not run"})
    await asyncio.sleep(0)
    assert session.replies == []
    assert binding.manager.current_turn is None
    await binding.close()
