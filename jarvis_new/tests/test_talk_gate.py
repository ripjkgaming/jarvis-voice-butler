import numpy as np

import talk_gate
from talk_gate import TalkOverGate


def block(rms: float, n: int = 1536, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    return (x / np.std(x) * rms).astype(np.int16)


def level(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


def run(gate, mic_rms, seconds, start, playback_rms=None, step=0.032):
    """Feed blocks for `seconds`; returns (end time, last output block)."""
    t, out = start, None
    for i in range(int(seconds / step)):
        if playback_rms is not None:
            gate.note_playback(playback_rms, t)
        out = gate.process(block(mic_rms, seed=i), t)
        t += step
    return t, out


def test_passes_everything_when_jarvis_is_silent() -> None:
    gate = TalkOverGate()
    b = block(3000)
    assert gate.process(b, 0.0) is b


def test_canteen_babble_and_echo_are_ducked_while_jarvis_speaks() -> None:
    gate = TalkOverGate()
    t, _ = run(gate, 2400, 8.0, 0.0)  # room learned: steady canteen
    # Jarvis talks; mic hears room + echo at a similar level.
    t, out = run(gate, 2600, 3.0, t, playback_rms=8000)
    assert level(out) < 2600 * 10 ** (-20 / 20)


def test_sir_leaning_in_cuts_through_and_keeps_word_endings() -> None:
    gate = TalkOverGate()
    t, _ = run(gate, 2400, 8.0, 0.0)
    t, _ = run(gate, 2600, 3.0, t, playback_rms=8000)
    gate.note_playback(8000, t)
    loud = block(2400 * 10 ** (14 / 20))
    assert gate.process(loud, t) is loud
    t += 0.032
    gate.note_playback(8000, t)
    tail = block(2600)
    assert gate.process(tail, t) is tail, "hangover keeps the word ending"


def test_gate_releases_after_jarvis_stops() -> None:
    gate = TalkOverGate()
    t, _ = run(gate, 2400, 3.0, 0.0, playback_rms=8000)
    t += talk_gate.TAIL_S + 0.05
    b = block(2400)
    assert gate.process(b, t) is b


def test_env_switch(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_TALK_GATE", "off")
    assert not talk_gate.gate_enabled()
    monkeypatch.delenv("JARVIS_TALK_GATE")
    assert talk_gate.gate_enabled()
