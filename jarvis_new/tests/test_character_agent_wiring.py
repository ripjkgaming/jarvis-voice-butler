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
    for kind, text in [("greet", "Good day, Sir. What do you require?"),
                       ("greet", "Yes, Sir?"), ("farewell", "Very good, Sir. Ring when you require me.")]:
        assert agent._canned(kind, text) == text
    monkeypatch.setenv("JARVIS_ACKS", "1")
    assert acks.next_line("search_the_web", now=1000.0) == acks.LINES[0]
    assert acks.next_line("search_the_web", now=2000.0) == acks.LINES[1]


def test_other_character_uses_persona(monkeypatch):
    _activate("ultron")
    monkeypatch.setattr(agent, "_with_memory", lambda s: s + "\n\n# Memory\nm")
    ins = agent._persona_instructions()
    assert ins.startswith("CHARACTER MODE: you are Ultron") and ins.endswith("# Memory\nm")
    ult = cr.get_runtime().chars["ultron"]
    assert agent._canned("greet", "Yes, Sir?") in ult.lines["greet"]
    assert acks.next_line("search_the_web", now=1000.0) in ult.lines["ack"]


def test_aliases_include_every_character_jarvis_unchanged():
    assert agent._name_called("Ultron?") and agent._name_called("hey friday")
    assert not agent._name_called("see you on friday afternoon")
    assert agent._name_called("Jeeves") and agent._name_called("jarvis")
    assert agent._is_dismiss("Ultron, dismiss") and agent._is_dismiss("Jarvis, dismiss")
    assert not agent._is_dismiss("Ultron, dismiss that notification")


def test_chess_does_not_swallow_switch_but_jarvis_wins(tmp_path, monkeypatch):
    started = []
    import switch_conductor

    monkeypatch.setattr(switch_conductor, "start_switch", lambda res, src="ui": started.append(res))

    async def run():
        eng, game, _sp, _sink = await build(tmp_path)
        try:
            assert eng._accept("switch to ultron", U()) is None
            assert started and started[0]["target"] == "ultron"
            cr.reset_runtime()
            started.clear()
            assert eng._accept("Hey Jarvis switch to ultron", U()) == "Hey Jarvis switch to ultron"
            assert not started
            assert eng._accept("hey ultron", U()) == "hey ultron"
        finally:
            await eng.stop()

    asyncio.run(run())
