"""Jarvis stays byte-identical; other characters get persona lines; chess defers switches."""

import asyncio
import sys

import pytest

sys.path.insert(0, "src")

import acks
import agent
import character_runtime as cr
from prompts import AGENT_INSTRUCTIONS
from test_chess_wake_priority import U, build


@pytest.fixture(autouse=True)
def _rt(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    cr.reset_runtime()
    acks.reset()
    yield
    cr.reset_runtime()


def _activate(cid):
    rt = cr.get_runtime()
    res = rt.switch(cid, "ui")
    rt.set_phase(cr.Phase.EXITING)
    rt.set_phase(cr.Phase.SIGNATURE)
    rt.set_phase(cr.Phase.ENTERING)
    rt.set_phase(cr.Phase.SETTLING)
    assert rt.complete(res["char_turn"])


def test_jarvis_prompt_greeting_ack_byte_identical(monkeypatch):
    monkeypatch.setattr(agent, "_with_memory", lambda s: s + "\n\n# Memory\nm")
    assert agent._persona_instructions() == AGENT_INSTRUCTIONS + "\n\n# Memory\nm"
    for kind, text in [
        ("greet", "Good day, Sir. What do you require?"),
        ("greet", "Yes, Sir?"),
        ("farewell", "Very good, Sir. Ring when you require me."),
    ]:
        assert agent._canned(kind, text) == text
    monkeypatch.setenv("JARVIS_ACKS", "1")
    assert acks.next_line("search_the_web", now=1000.0) == acks.LINES[0]
    assert acks.next_line("search_the_web", now=2000.0) == acks.LINES[1]
