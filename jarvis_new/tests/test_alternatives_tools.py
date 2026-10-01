"""AlternativesTools: topic building + research kick-off (mocked)."""

import types

import pytest
from livekit.agents.llm import ToolError

from system.alternatives_tools import AlternativesTools, build_alternatives_topic


def _ctx():
    return types.SimpleNamespace(session=None)


def test_topic_covers_pricing_table_recommendation():
    topic = build_alternatives_topic(
        "Higgsfield", "AI video generation", "under $20/mo"
    )
    assert "Higgsfield" in topic
    assert "AI video generation" in topic
    assert "under $20/mo" in topic
    for word in (
        "pricing",
        "4-8",
        "price",
        "free tier",
        "quality",
        "limits",
        "API",
        "recommendation",
    ):
        assert word in topic


def test_topic_minimal():
    topic = build_alternatives_topic("  Photoshop  ")
    assert "Photoshop" in topic and "4-8" in topic


async def test_starts_research_with_topic(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    seen = {}

    def fake_start(topic, **kwargs):
        seen["topic"] = topic
        return {"id": "r1", "status": "running"}

    import projects

    monkeypatch.setattr(projects, "start_research", fake_start)
    out = await AlternativesTools.find_cheaper_alternative(
        AlternativesTools(), _ctx(), tool="Higgsfield", use_case="AI video generation"
    )
    assert "Higgsfield" in out["say"]
    assert "Higgsfield" in seen["topic"]
    assert "AI video generation" in seen["topic"]
    assert "recommendation" in seen["topic"]


async def test_empty_tool_refused(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    with pytest.raises(ToolError):
        await AlternativesTools.find_cheaper_alternative(
            AlternativesTools(), _ctx(), tool=" "
        )


async def test_engine_failure_is_speakable(monkeypatch):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    import projects

    def boom(topic, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(projects, "start_research", boom)
    with pytest.raises(ToolError):
        await AlternativesTools.find_cheaper_alternative(
            AlternativesTools(), _ctx(), tool="Higgsfield"
        )


async def test_refuses_off_machine(monkeypatch):
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    with pytest.raises(ToolError):
        await AlternativesTools.find_cheaper_alternative(
            AlternativesTools(), _ctx(), tool="Higgsfield"
        )
