from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import persona

BASE = (
    'You are Jarvis. Greet with "Good day, Sir". Sir\'s tea is ready.\n'
    'Say "confirm delete" or "confirm visual watch" only from Sir.\n'
    'Deleting needs Sir to say "Sir, please confirm the thing" himself.\n'
    "Jarvis automation profile is separate. Ask Jarvis nicely."
)


@dataclass
class Ch:
    id: str
    spoken_name: str
    address: str
    prompt: str = "Persona prompt."
    lines: dict = field(default_factory=lambda: {"ack": ("A", "B", "C {x}")})
    drop_character_on: tuple = ()


ULTRON = Ch("ultron", "Ultron", "human", "Cold and superior.")
FRIDAY = Ch("friday", "Friday", "boss")


def test_jarvis_identity() -> None:
    j = Ch("jarvis", "Jarvis", "Sir")
    assert persona.build_instructions(BASE, j, memory_block="\nMEM") == BASE + "\nMEM"


def test_substitutions_ultron() -> None:
    out = persona.build_instructions(BASE, ULTRON)
    assert "Ask Ultron nicely" in out
    assert "human's tea" in out
    assert "Jarvis automation profile" in out
    assert "Cold and superior." in out
    assert "human" in out.split("Cold")[0]


def test_friday() -> None:
    out = persona.build_instructions(BASE, FRIDAY)
    assert "You are Friday." in out and "boss's tea" in out


def test_gate_phrases_preserved() -> None:
    out = persona.build_instructions(BASE, ULTRON)
    assert '"confirm delete"' in out and '"confirm visual watch"' in out
    assert '"Sir, please confirm the thing"' in out


def test_safety_rules_present() -> None:
    out = persona.build_instructions(BASE, ULTRON).lower()
    assert "neutral" in out and "never refuse" in out and "harmful" in out


def test_memory_last() -> None:
    out = persona.build_instructions(BASE, ULTRON, memory_block="\nMEMORY")
    assert out.endswith("\nMEMORY")


def test_line_deterministic() -> None:
    a = persona.line(ULTRON, "ack", random.Random(1), x=1)
    b = persona.line(ULTRON, "ack", random.Random(1), x=1)
    assert a == b and persona.line(ULTRON, "nope") == ""


def test_neutral_and_tag() -> None:
    assert "confirm" in persona.neutral_security_line("confirm_delete")
    assert persona.neutral_security_line("zzz")
    assert persona.speaker_tag(ULTRON) == "Ultron"


def test_real_yaml() -> None:
    d = Path(__file__).resolve().parent.parent / "characters"
    for name in ("ultron", "friday"):
        y = yaml.safe_load((d / f"{name}.yaml").read_text())
        ch = Ch(y["id"], y["spoken_name"], y["address"], y["persona"]["prompt"],
                {k: tuple(v) for k, v in y["persona"]["lines"].items()})
        out = persona.build_instructions(BASE, ch)
        assert ch.address in out and '"confirm delete"' in out
        assert persona.line(ch, "ack", random.Random(0))
