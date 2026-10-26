"""Transient delivery failures must not permanently consume reminders."""

import pytest

from proactive import engine
from proactive.signals import Signal


@pytest.mark.parametrize("urgency", ["high", "normal", "low"])
@pytest.mark.parametrize("raises", [False, True])
def test_failed_delivery_is_retryable_and_success_is_deduplicated(
    monkeypatch, urgency, raises
):
    import phone_calls

    monkeypatch.setattr(phone_calls, "offer_signal", lambda *args: None)
    available = False
    attempts = []

    def deliver(signal):
        attempts.append(signal.key)
        if raises and not available:
            raise OSError("Synthetic transport outage")
        return available

    signal = Signal(
        kind="calendar",
        key="meeting:10min",
        title="Meeting soon",
        urgency=urgency,
        ts=100,
    )
    worker = engine.ProactiveEngine(
        [lambda now: [signal]],
        context_fn=lambda *_: engine.Context(idle_s=0),
        say=deliver,
        toast=deliver,
        hud=deliver,
        log=lambda *_: None,
    )
    worker.run_once(100)
    state = engine.load_state()
    assert signal.key not in state.get("delivered", {})
    assert sum(state.get("spoken", {}).values()) == 0
    assert not state.get("pending_acks")
    available = True
    worker.run_once(130)
    assert signal.key in engine.load_state()["delivered"]
    count = len(attempts)
    worker.run_once(160)
    assert len(attempts) == count
