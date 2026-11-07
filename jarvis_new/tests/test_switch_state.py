from __future__ import annotations

import itertools

import pytest

from switch_state import IllegalTransitionError, Phase, SwitchMachine

IDS = ["jarvis", "friday", "edith"]


def _m(hints=None) -> SwitchMachine:
    return SwitchMachine(IDS, hint=hints.append if hints is not None else None)


def _run_to_settling(m: SwitchMachine, t: int) -> None:
    m.prewarm_done(t, True)
    m.advance(Phase.EXITING)
    m.advance(Phase.SIGNATURE)
    assert m.ready_gate(t)
    m.advance(Phase.ENTERING)
    m.advance(Phase.SETTLING)


def test_boot_default() -> None:
    m = SwitchMachine(["friday"])
    assert m.active == "jarvis" and m.phase is Phase.IDLE and m.turn_id == 0
    assert m.snapshot() == {
        "active": "jarvis",
        "target": "jarvis",
        "phase": "idle",
        "turn_id": 0,
    }


@pytest.mark.parametrize("a,b", list(itertools.permutations(IDS, 2)))
def test_directed_pairs(a: str, b: str) -> None:
    hints: list[str] = []
    m = _m(hints)
    if a != "jarvis":
        t = m.request(a, "voice").turn_id
        _run_to_settling(m, t)
        assert m.complete(t)
    d = m.request(b, "text")
    assert d.kind == "started" and d.from_id == a and d.to_id == b
    assert d.play_exit_line and d.replay_signature
    _run_to_settling(m, d.turn_id)
    assert m.complete(d.turn_id)
    assert m.active == b and m.phase is Phase.IDLE and hints[-1] == b


def test_already_active_and_unknown() -> None:
    m = _m()
    d = m.request("jarvis", "ui")
    assert d.kind == "already_active" and m.turn_id == 0 and m.phase is Phase.IDLE
    assert m.request("nobody").kind == "ignored_unknown" and m.turn_id == 0


def test_retarget_chain_single_transition() -> None:
    m = _m()
    d1 = m.request("friday")
    m.advance(Phase.EXITING)
    m.set_progress(0.3)
    d2 = m.request("edith")
    assert d2.kind == "retargeted" and d2.turn_id == d1.turn_id + 1
    assert d2.from_progress == 0.3 and m.progress == 0.3
    assert d2.replay_signature is True
    assert m.target == "edith" and m.active == "jarvis"
    assert m.should_greet(d1.turn_id) is False
    assert m.should_greet(d2.turn_id) is True
    assert m.should_greet(d2.turn_id) is False
    m.prewarm_done(d1.turn_id, True)  # stale, ignored
    m.prewarm_done(d2.turn_id, True)
    m.advance(Phase.SIGNATURE)
    assert m.ready_gate(d2.turn_id)
    m.advance(Phase.ENTERING)
    m.advance(Phase.SETTLING)
    assert m.complete(d1.turn_id) is False
    assert m.complete(d2.turn_id) is True
    assert m.active == "edith"


def test_retarget_skips_signature_late() -> None:
    m = _m()
    m.request("friday")
    m.set_progress(0.8)
    assert m.request("edith").replay_signature is False
    m2 = _m()
    t = m2.request("friday").turn_id
    m2.prewarm_done(t, True)
    m2.advance(Phase.EXITING)
    m2.advance(Phase.SIGNATURE)
    m2.advance(Phase.ENTERING)
    assert m2.request("edith").replay_signature is False


def test_stale_complete_ignored() -> None:
    m = _m()
    d1 = m.request("friday")
    m.request("edith")
    assert m.complete(d1.turn_id) is False
    assert m.active == "jarvis" and m.phase is Phase.PREPARING


def test_rollback_on_failed_prewarm() -> None:
    hints: list[str] = []
    m = _m(hints)
    d = m.request("friday")
    r = m.prewarm_done(d.turn_id, False)
    assert r is not None and r.kind == "rollback" and r.to_id == "jarvis"
    assert m.phase is Phase.ROLLBACK and m.target == "jarvis"
    assert m.complete(d.turn_id)
    assert m.active == "jarvis" and m.phase is Phase.IDLE


def test_ready_gate_without_prewarm_rolls_back() -> None:
    m = _m()
    d = m.request("friday")
    m.advance(Phase.EXITING)
    m.advance(Phase.SIGNATURE)
    assert m.ready_gate(d.turn_id) is False
    assert m.phase is Phase.ROLLBACK


def test_input_buffering() -> None:
    m = _m()
    m.buffer_input("hi")
    assert m.need_fast_forward() is False
    m.request("friday")
    assert m.need_fast_forward() is True
    assert m.drain_input() == ["hi"]
    assert m.need_fast_forward() is False


def test_illegal_transitions() -> None:
    m = _m()
    with pytest.raises(IllegalTransitionError):
        m.advance(Phase.ENTERING)
    m.request("friday")
    with pytest.raises(IllegalTransitionError):
        m.advance(Phase.SETTLING)
    with pytest.raises(IllegalTransitionError):
        m.advance(Phase.IDLE)
