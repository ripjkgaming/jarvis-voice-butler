"""Hermetic tests for spoken acks and barge-in (IRONMAN_SPEC §4)."""

from __future__ import annotations

import pytest

import acks
import agent


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_ACKS", raising=False)
    acks.reset()
    yield
    acks.reset()


def test_known_slow_then_learned() -> None:
    assert acks.expected_slow("search_the_web")
    assert not acks.expected_slow("tell_time")
    for _ in range(5):
        acks.record("tell_time", 4.0)
    assert acks.expected_slow("tell_time")
    for _ in range(10):
        acks.record("search_the_web", 0.2)
    assert not acks.expected_slow("search_the_web")
    acks.reset()
    assert acks.expected_slow("tell_time")  # persisted to disk


def test_rate_limit_and_rotation(monkeypatch) -> None:
    assert acks.next_line("search_the_web", now=100.0) == "On it, Sir."
    assert acks.next_line("search_the_web", now=105.0) is None
    assert acks.next_line("search_the_web", now=130.0) == "One moment, Sir."
    assert acks.next_line("tell_time", now=500.0) is None
    monkeypatch.setenv("JARVIS_ACKS", "0")
    assert acks.next_line("search_the_web", now=900.0) is None


def test_say_ack_uses_run_context_session() -> None:
    said = []

    class Session:
        def say(self, text, **kw):
            said.append((text, kw))

    class Ctx:
        session = Session()

    agent._say_ack("search_the_web", (object(), Ctx()), {})
    assert said == [
        ("On it, Sir.", {"add_to_chat_ctx": False, "allow_interruptions": True})
    ]
    agent._say_ack("tell_time", (), {"context": Ctx()})
    assert len(said) == 1


def test_say_ack_never_raises() -> None:
    class Broken:
        @property
        def session(self):
            raise RuntimeError("no tts")

    agent._say_ack("search_the_web", (Broken(),), {})


def test_barge_in(monkeypatch) -> None:
    assert agent.barge_in("vad") == {
        "mode": "vad",
        "min_duration": 0.3,
        "resume_false_interruption": True,
    }
    monkeypatch.setenv("JARVIS_BARGE_IN_S", "0.05")
    assert agent.barge_in("adaptive")["min_duration"] == 0.1
    monkeypatch.setenv("JARVIS_BARGE_IN_S", "junk")
    assert agent.barge_in("vad")["min_duration"] == 0.3
