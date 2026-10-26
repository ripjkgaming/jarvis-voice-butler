from types import SimpleNamespace

import pytest
from goodnight_ingress import install


class Speech:
    def __init__(self):
        self.callbacks = []
        self.finished = False

    def done(self):
        return self.finished

    def add_done_callback(self, callback):
        self.callbacks.append(callback)

    def remove_done_callback(self, callback):
        self.callbacks.remove(callback)

    def complete(self):
        self.finished = True
        for callback in self.callbacks:
            callback(self)


class Rig:
    def __init__(self):
        self.events = {}
        self.inputs = []
        self.said = []
        self.activity = []
        self.cancelled = []
        self.states = []
        self.allowed = True
        self.now = 100.0
        self.wall = 1000.0
        self.speech = Speech()
        self.session = SimpleNamespace(
            agent_state="listening",
            user_state="listening",
            on=lambda name, callback: self.events.update({name: callback}),
            off=lambda name, callback: self.events.pop(name),
            say=self.say,
        )
        self.host = SimpleNamespace(
            controller=SimpleNamespace(
                cancel=self.cancelled.append,
                activity=lambda: self.activity.append(self.now),
            ),
            input=lambda text, **kw: self.inputs.append((text, kw)) or "acknowledged",
            close=lambda: self.cancelled.append("closed"),
        )
        self.binding = install(
            self.session,
            self.host,
            source_is_owner=lambda: self.allowed,
            conversation_update=self.states.append,
            clock=lambda: self.now,
            wall_clock=lambda: self.wall,
        )

    def say(self, text):
        self.said.append(text)
        return self.speech

    def final(self, **extra):
        event = SimpleNamespace(
            transcript="goodnight", is_final=True, created_at=self.wall, **extra
        )
        self.events["user_input_transcribed"](event)
        return event


def test_close_revokes_and_late_input_cannot_rearm():
    r = Rig()
    callback = r.events["user_input_transcribed"]
    r.final(item_id="turn-1")
    r.events["close"](SimpleNamespace())
    callback(
        SimpleNamespace(
            transcript="goodnight", is_final=True, created_at=r.wall, item_id="turn-2"
        )
    )
    assert len(r.inputs) == 1
    assert r.cancelled[-1] == "closed"
    assert r.binding.snapshot()["closed"] is True
    assert r.binding.conversation_clear() is False


def test_remove_cancels_idempotently_and_late_speech_callback_cannot_publish():
    r = Rig()
    r.final(item_id="turn-1")
    r.binding()
    assert not r.events and r.cancelled[-1] == "closed"
    count = len(r.states)
    assert not r.speech.callbacks
    r.speech.complete()
    r.binding()
    assert len(r.states) == count


def test_same_final_id_dedup_does_not_repeat_arm_ack_or_activity():
    r = Rig()
    r.final(item_id="turn-1")
    count = len(r.activity)
    r.now += 100
    r.wall += 100
    r.final(item_id="turn-1")
    assert len(r.inputs) == len(r.said) == 1
    assert len(r.activity) == count


def test_same_timestamp_dedup_without_item_id_but_new_turn_allowed():
    r = Rig()
    r.final()
    r.final()
    assert len(r.inputs) == 1
    r.wall += 1
    r.final()
    assert len(r.inputs) == 2


def test_object_identity_dedup_without_provider_identity():
    r = Rig()
    event = SimpleNamespace(transcript="goodnight", is_final=True)
    callback = r.events["user_input_transcribed"]
    callback(event)
    callback(event)
    assert len(r.inputs) == 1


def test_partial_is_activity_but_never_arms():
    r = Rig()
    r.events["user_input_transcribed"](
        SimpleNamespace(transcript="goodnight", is_final=False)
    )
    assert not r.inputs and r.activity


def test_guest_and_authority_failure_revoke_before_input():
    r = Rig()
    r.allowed = False
    r.final(item_id="turn-1")
    assert not r.inputs and r.cancelled[-1] == "closed"


@pytest.mark.parametrize("created", [900.0, 1100.0, float("nan"), "bad"])
def test_stale_future_or_invalid_event_timestamp_revokes(created):
    r = Rig()
    r.events["user_input_transcribed"](
        SimpleNamespace(transcript="goodnight", is_final=True, created_at=created)
    )
    assert not r.inputs and r.cancelled[-1] == "closed"


def test_preserve_sdk_state_and_acknowledgement_completion():
    r = Rig()
    assert r.binding.conversation_clear() is True
    r.events["user_state_changed"](SimpleNamespace(new_state="speaking"))
    assert r.binding.snapshot()["user_state"] == "speaking"
    assert r.binding.conversation_clear() is False
    r.events["user_state_changed"](SimpleNamespace(new_state="listening"))
    r.events["agent_state_changed"](SimpleNamespace(new_state="thinking"))
    assert r.binding.conversation_clear() is False
    r.events["agent_state_changed"](SimpleNamespace(new_state="listening"))
    r.final(item_id="turn-1")
    assert r.binding.snapshot()["pending_speech"] == 1
    assert r.binding.conversation_clear() is False
    r.speech.complete()
    assert r.binding.snapshot()["pending_speech"] == 0
    assert r.binding.conversation_clear() is True
    assert all("transcript" not in state for state in r.states)


def test_unknown_initial_and_event_state_fail_closed():
    r = Rig()
    r.events["agent_state_changed"](SimpleNamespace(new_state="unknown"))
    assert r.binding.snapshot()["agent_state"] is None
    assert r.binding.conversation_clear() is None


def test_dedup_overflow_revokes_instead_of_evicting_old_events():
    r = Rig()
    r.binding.max_seen = 1
    r.final(item_id="turn-1")
    r.final(item_id="turn-2")
    assert len(r.inputs) == 1 and r.cancelled[-1] == "closed"


def test_async_say_is_refused_without_unawaited_coroutine():
    r = Rig()

    async def unexpected_async_say(text):
        raise AssertionError("must never run")

    r.session.say = unexpected_async_say
    r.final(item_id="turn-1")
    assert r.cancelled[-1] == "closed"


def test_conversation_observer_failure_revokes():
    r = Rig()
    r.binding.conversation_update = lambda state: (_ for _ in ()).throw(RuntimeError())
    r.events["agent_state_changed"](SimpleNamespace(new_state="speaking"))
    assert r.cancelled[-1] == "closed"


def test_partial_host_dispatch_failure_revokes():
    r = Rig()
    r.host.input = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError())
    r.final(item_id="turn-1")
    assert r.cancelled[-1] == "closed" and not r.said
