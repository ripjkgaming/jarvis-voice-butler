"""Hermetic tests for src/system/recall_tools.py (reads local logs only)."""

from __future__ import annotations

import json
import time

import pytest
from livekit.agents.llm import ToolError

import mail_log
from system import recall_tools
from system.recall_tools import RecallTools


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_STUDY_PLAN", str(tmp_path / "none.json"))
    return tmp_path


def _wa(home, *entries) -> None:
    with (home / "wa_digest.jsonl").open("a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_describe_wa_sent_handoff_and_dry(home) -> None:
    now = time.time()
    _wa(
        home,
        {
            "ts": now - 600,
            "chat": "Aarav",
            "incoming": ["u on tonight?"],
            "reply": "ya prob",
            "sent": True,
            "as_owner": True,
        },
        {
            "ts": now - 300,
            "chat": "Mum",
            "why": "handoff",
            "for_owner": "she sounds upset",
        },
        {
            "ts": now - 200,
            "chat": "Kabir",
            "reply": "x",
            "sent": False,
            "why": "dry-run",
        },
    )
    entries = recall_tools.wa_entries(now - 3600)
    say = recall_tools.describe_wa(entries, now)
    assert "Aarav at" in say and 'I replied as you "ya prob"' in say
    assert "Mum at" in say and "she sounds upset" in say
    assert "dry-run" not in say  # something was really sent
    only = recall_tools.wa_entries(now - 3600, chat="aar")
    assert [e["chat"] for e in only] == ["Aarav"]
    dry = recall_tools.describe_wa(
        recall_tools.wa_entries(now - 3600, chat="kabir"), now
    )
    assert "dry-run" in dry


@pytest.mark.asyncio
async def test_whatsapp_replies_tool(home) -> None:
    tools = RecallTools()
    none = await RecallTools.whatsapp_replies(tools, None, chat="Aarav")  # type: ignore[arg-type]
    assert "haven't replied to Aarav" in none["say"]
    _wa(
        home,
        {
            "ts": time.time(),
            "chat": "Aarav",
            "incoming": ["hi"],
            "reply": "yo",
            "sent": True,
        },
    )
    out = await RecallTools.whatsapp_replies(tools, None, chat="aarav")  # type: ignore[arg-type]
    assert '"yo"' in out["say"]


@pytest.mark.asyncio
async def test_email_activity_tool(home) -> None:
    tools = RecallTools()
    mail_log.record("acked", to="t@school.edu", subject="Re: Form", body="auto ack")
    mail_log.record("flagged", sender="Teacher", subject="Form due")
    mail_log.record("sent", to="bob@x.com", subject="Re: Game", body="see you at 5")
    replied = await RecallTools.email_activity(tools, None, kind="replied")  # type: ignore[arg-type]
    assert "I sent bob@x.com: Re: Game" in replied["say"]
    assert "auto-acknowledged t@school.edu" in replied["say"]
    assert "flagged" not in replied["say"]
    bob = await RecallTools.email_activity(tools, None, who="bob")  # type: ignore[arg-type]
    assert "school" not in bob["say"]
    with pytest.raises(ToolError):
        await RecallTools.email_activity(tools, None, kind="weird")  # type: ignore[arg-type]
    none = await RecallTools.email_activity(tools, None, kind="drafted")  # type: ignore[arg-type]
    assert "No email activity" in none["say"]


def test_recap_combines_everything(home, monkeypatch) -> None:
    import exams

    now = time.time()
    _wa(
        home,
        {
            "ts": now - 60,
            "chat": "Aarav",
            "reply": "ok",
            "sent": True,
            "pass_along": True,
            "for_owner": "asks about Friday",
        },
        {"ts": now - 30, "chat": "Mum", "why": "handoff", "for_owner": "call her"},
    )
    mail_log.record("flagged", sender="Teacher", subject="Form due")
    monkeypatch.setattr(
        exams,
        "next_exam",
        lambda: {
            "title": "Maths",
            "date": "2099-01-01",
            "start": "",
            "end": "",
            "location": "",
        },
    )
    say = recall_tools.recap(12, now)
    assert "I replied 1 time to Aarav" in say
    assert "Mum (call her)" in say
    assert "Aarav: asks about Friday" in say
    assert "Urgent mail: 1" in say
    assert "Next exam: Maths" in say


def test_recap_quiet(home) -> None:
    assert recall_tools.recap(6) == "Nothing needed you in the last 6 hours, Sir."


def test_registered_on_assistant_tool_lists() -> None:
    from system.exam_tools import ExamTools

    ids = [t.id for t in RecallTools().tools] + [t.id for t in ExamTools().tools]
    assert ids == ["whatsapp_replies", "email_activity", "catch_me_up", "exam_schedule"]
