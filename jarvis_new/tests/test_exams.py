"""Hermetic tests for src/exams.py and the exam_schedule voice tool."""

from __future__ import annotations

import datetime as dt
import json

import pytest
from livekit.agents.llm import ToolError

import exams
from system.exam_tools import ExamTools

TODAY = dt.date(2026, 10, 1)  # a Thursday


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jh"))
    monkeypatch.setenv("JARVIS_STUDY_PLAN", str(tmp_path / "plan.json"))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(exams, "_today", lambda: TODAY)
    return tmp_path


def test_add_dedupes_and_updates(home) -> None:
    assert exams.add({"title": "Physics Paper 2", "date": "2026-10-14"}) == "added"
    assert (
        exams.add({"title": "physics  paper 2", "date": "2026-10-14", "start": "09:00"})
        == "updated"
    )
    rows = exams.load()
    assert len(rows) == 1 and rows[0]["start"] == "09:00"
    assert exams.add({"title": "", "date": "2026-10-14"}) == "invalid"
    assert exams.add({"title": "X", "date": "14 Oct"}) == "invalid"


def test_merges_study_plan_and_sorts(home) -> None:
    (home / "plan.json").write_text(
        json.dumps({"exams": {"2026-10-05": {"papers": "Maths P1", "status": "exam"}}})
    )
    exams.add({"title": "Chemistry P4", "date": "2026-10-20"})
    exams.add({"title": "Old mock", "date": "2026-09-01"})
    up = exams.upcoming()
    assert [e["title"] for e in up] == ["Maths P1", "Chemistry P4"]
    assert exams.next_exam()["title"] == "Maths P1"
    assert [e["title"] for e in exams.upcoming(days=7)] == ["Maths P1"]


def test_find_and_describe(home) -> None:
    exams.add(
        {
            "title": "Physics Paper 2",
            "date": "2026-10-02",
            "start": "09:00",
            "end": "10:45",
            "location": "Hall B",
        }
    )
    found = exams.find("my physics exam")
    assert len(found) == 1
    assert exams.describe(found[0]) == (
        "Physics Paper 2, Fri 02 Oct at 09:00-10:45 in Hall B (tomorrow)"
    )
    assert exams.find("biology") == []


def test_import_events_keeps_exam_like_or_all(home) -> None:
    n = exams.import_events(
        [
            {"title": "Biology mock paper 1", "date": "2026-11-02", "start": "08:30"},
            {"title": "Sports day", "date": "2026-11-03"},
            {"title": "bad", "date": "nope"},
        ]
    )
    assert n == 1 and [e["title"] for e in exams.load()] == ["Biology mock paper 1"]
    n = exams.import_events([{"title": "Session A", "date": "2026-11-05"}])
    assert n == 1


def test_summary_flags_double_days(home) -> None:
    exams.add({"title": "Maths P1", "date": "2026-10-05", "start": "09:00"})
    exams.add({"title": "Maths P2", "date": "2026-10-05", "start": "13:00"})
    say = exams.summary(days=30)
    assert say.startswith("2 exams in the next 30 days")
    assert "Double days: Mon 05 Oct" in say
    assert exams.briefing_line().startswith("Next exam: Maths P1")


def test_remove(home) -> None:
    exams.add({"title": "Bio mock", "date": "2026-10-09"})
    exams.add({"title": "Chem mock", "date": "2026-10-09"})
    gone = exams.remove("bio")
    assert [e["title"] for e in gone] == ["Bio mock"]
    assert [e["title"] for e in exams.load()] == ["Chem mock"]
    assert exams.remove("") == []


def test_parse_time_and_date() -> None:
    assert exams.parse_time("9am") == "09:00"
    assert exams.parse_time("2:30 pm") == "14:30"
    assert exams.parse_time("13:05") == "13:05"
    assert exams.parse_time("12am") == "00:00"
    assert exams.parse_time("9") == ""
    assert exams.parse_date("tomorrow", TODAY) == "2026-10-02"
    assert exams.parse_date("monday", TODAY) == "2026-10-05"
    assert exams.parse_date("thursday", TODAY) == "2026-10-08"
    assert exams.parse_date("14 Oct", TODAY) == "2026-10-14"
    assert exams.parse_date("Jan 5", TODAY) == "2027-01-05"
    assert exams.parse_date("2026-12-01", TODAY) == "2026-12-01"
    assert exams.parse_date("someday", TODAY) == ""


def test_reminders_evening_before_and_morning_of(home) -> None:
    exams.add({"title": "Physics P2", "date": "2026-10-02", "start": "09:00"})
    sent: list = []

    def send(text, **kw):
        sent.append((text, kw))

    assert exams.remind_once(dt.datetime(2026, 10, 1, 12, 0), send) == 0
    assert exams.remind_once(dt.datetime(2026, 10, 1, 19, 0), send) == 1
    assert "Exam tomorrow: Physics P2" in sent[0][0]
    assert exams.remind_once(dt.datetime(2026, 10, 1, 21, 0), send) == 0
    assert exams.remind_once(dt.datetime(2026, 10, 2, 7, 0), send) == 1
    assert "Exam today" in sent[1][0]
    # Once the exam has started there is nothing left to remind.
    exams.add({"title": "Chem P1", "date": "2026-10-02", "start": "06:30"})
    assert exams.remind_once(dt.datetime(2026, 10, 2, 7, 30), send) == 0


@pytest.mark.asyncio
async def test_exam_tool_actions(home) -> None:
    tools = ExamTools()
    empty = await ExamTools.exam_schedule(tools, None)  # type: ignore[arg-type]
    assert "No exams" in empty["say"]
    added = await ExamTools.exam_schedule(  # type: ignore[arg-type]
        tools, None, action="add", subject="Maths Paper 2", date="14 Oct", time="9am"
    )
    assert added["say"] == "Added: Maths Paper 2, Wed 14 Oct at 09:00 (in 13 days)."
    nxt = await ExamTools.exam_schedule(tools, None)  # type: ignore[arg-type]
    assert "Maths Paper 2" in nxt["say"]
    found = await ExamTools.exam_schedule(  # type: ignore[arg-type]
        tools, None, action="find", subject="maths"
    )
    assert "in 13 days" in found["say"]
    missing = await ExamTools.exam_schedule(  # type: ignore[arg-type]
        tools, None, action="find", subject="history"
    )
    assert "no history exam" in missing["say"]
    listed = await ExamTools.exam_schedule(tools, None, action="upcoming")  # type: ignore[arg-type]
    assert listed["say"].startswith("1 exam in the next 30 days")
    with pytest.raises(ToolError):
        await ExamTools.exam_schedule(tools, None, action="add", subject="X")  # type: ignore[arg-type]
    with pytest.raises(ToolError):
        await ExamTools.exam_schedule(  # type: ignore[arg-type]
            tools, None, action="add", subject="X", date="tomorrow", time="soonish"
        )
    gone = await ExamTools.exam_schedule(  # type: ignore[arg-type]
        tools, None, action="remove", subject="maths"
    )
    assert gone["say"].startswith("Removed 1")
