"""Hermetic tests for task lookup steps (read-only own-data access)."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest
from test_agent_tasks import codex_output

import agent_tasks as at
import chatgpt_cli
import task_sources as ts


def b64(text: str | bytes) -> str:
    raw = text.encode() if isinstance(text, str) else text
    return base64.urlsafe_b64encode(raw).decode()


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_CHATGPT", "1")
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_AUTO_APPROVE", "0")
    monkeypatch.setenv("JARVIS_TASK_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setattr(chatgpt_cli.shutil, "which", lambda n: "/usr/bin/codex")
    return tmp_path


PLAN = {
    "title": "Trip packing list",
    "save_as": "trip-packing-list.md",
    "steps": [
        {"title": "Read trip emails", "kind": "lookup", "detail": "Chiang Mai trip"},
        {"title": "Write the list", "kind": "write", "detail": "packing list"},
    ],
}


class Sources:
    """Stand-in for the task_sources module."""

    def __init__(self, text="EMAIL kit list: rash vest", errors=(), hits=1):
        self.text, self.errors, self.hits = text, list(errors), hits
        self.queried = []

    def enabled(self):
        return True

    def parse_queries(self, raw):
        return ts.parse_queries(raw)

    def fetch(self, queries):
        self.queried.append(queries)
        return self.text, self.errors, self.hits


def runner(plan=PLAN, queries=None):
    queries = queries or {"queries": [{"source": "gmail", "query": "Chiang Mai"}]}

    def run(argv, **kw):
        system = json.loads(
            next(
                v.split("=", 1)[1]
                for v in argv
                if v.startswith("developer_instructions=")
            )
        )
        prompt = kw["input"]
        tools = "web" if 'web_search="live"' in argv else ""
        run.calls.append((system[:22], tools, prompt))
        if system.startswith("You plan"):
            out = json.dumps(plan)
        elif system.startswith("You choose read-only"):
            out = json.dumps(queries)
        elif system.startswith("You decide whether"):
            out = json.dumps(run.followup)
        elif system.startswith("You read search"):
            if run.no_match:
                out = "NO MATCH: only a marketing preset."
            else:
                out = "FACTS: rash vest required; depart 1 Nov"
        elif False:
            out = "FACTS: rash vest required; depart 1 Nov"
        else:
            out = "PACKING LIST\n- rash vest\n- 6 days underwear"
        return subprocess.CompletedProcess(argv, 0, codex_output(out), "")

    run.calls = []
    run.followup = {"queries": []}
    run.no_match = False
    return run


def test_parse_plan_accepts_lookup_and_sanitises_save_as() -> None:
    plan = at.parse_plan(json.dumps(PLAN))
    assert [s["kind"] for s in plan["steps"]] == ["lookup", "write"]
    assert plan["save_as"] == "trip-packing-list.md"
    for bad in ("../../etc/passwd", "x.sh", ".md", "/abs/p.md", "", None):
        evil = at.parse_plan(json.dumps({**PLAN, "save_as": bad}))
        assert "/" not in evil["save_as"]
    assert at.safe_filename("../../etc/passwd") == ""
    assert at.safe_filename("a/b/list.md") == "list.md"
    assert at.safe_filename("x.sh") == ""
    assert at.safe_filename("My List.TXT") == "My List.txt"


def test_lookup_runs_in_process_then_write_and_saves(home, monkeypatch) -> None:
    src = Sources()
    monkeypatch.setitem(__import__("sys").modules, "task_sources", src)
    run = runner()
    task = at.start("packing list for the Chiang Mai trip", run, background=False)
    task = at.get(task["id"])
    assert task["status"] == "done"
    assert [s["status"] for s in task["steps"]] == ["done", "done"]
    # worker never gets tools for the mail read; only the model JSON picks queries
    assert all(c[1] == "" for c in run.calls)
    assert src.queried == [[{"source": "gmail", "query": "Chiang Mai"}]]
    # extraction saw the fenced search results; the write step saw the facts
    assert "<<<SEARCH RESULTS>>>" in run.calls[3][2]
    assert "kit list: rash vest" in run.calls[3][2]
    assert "FACTS: rash vest" in run.calls[4][2]
    saved = (home / "out" / "trip-packing-list.md").read_text()
    assert "rash vest" in saved and task["deliverable"].endswith("trip-packing-list.md")
    assert "Saved:" in task["report"]


def test_lookup_with_no_hits_fails_instead_of_inventing(home, monkeypatch) -> None:
    src = Sources(text="## gmail search: x\nNO RESULTS", hits=0)
    monkeypatch.setitem(__import__("sys").modules, "task_sources", src)
    run = runner()
    task = at.get(at.start("packing list", run, background=False)["id"])
    assert task["status"] == "failed"
    assert "nothing" in task["error"].lower()
    assert len(run.calls) == 2  # plan + query choice; no followup, extraction, write
    assert not (home / "out").exists()


def test_lookup_source_error_is_reported(home, monkeypatch) -> None:
    src = Sources(text="", errors=["gmail: Gmail sign-in expired"], hits=0)
    monkeypatch.setitem(__import__("sys").modules, "task_sources", src)
    task = at.get(at.start("packing list", runner(), background=False)["id"])
    assert task["status"] == "failed"
    assert "sign-in expired" in task["error"]


def test_partial_source_error_still_flows_with_note(home, monkeypatch) -> None:
    src = Sources(errors=["calendar: Calendar is not reachable"], hits=2)
    monkeypatch.setitem(__import__("sys").modules, "task_sources", src)
    run = runner()
    task = at.get(at.start("packing list", run, background=False)["id"])
    assert task["status"] == "done"
    assert "SOURCE ERRORS: calendar" in task["steps"][0]["output"]


def test_kill_switch(home, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_TASK_LOOKUP", "0")
    run = runner()
    task = at.get(at.start("packing list", run, background=False)["id"])
    assert task["status"] == "failed" and "turned off" in task["error"]
    assert len(run.calls) == 1  # only the plan


def test_unusable_query_choice_fails(home, monkeypatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "task_sources", Sources())
    run = runner(queries={"queries": [{"source": "shell", "query": "rm -rf /"}]})
    task = at.get(at.start("packing list", run, background=False)["id"])
    assert task["status"] == "failed"


def test_deliverable_never_overwrites_or_escapes(home) -> None:
    a = at.save_deliverable("list.md", "one")
    b = at.save_deliverable("list.md", "two")
    assert a != b and Path(a).read_text().strip() == "one"
    assert b.endswith("list (2).md")
    assert at.save_deliverable("../escape.md", "x").startswith(str(home / "out"))
    assert at.save_deliverable("bad.sh", "x") is None
    assert at.save_deliverable("empty.md", "  ") is None


def test_no_save_without_request(home, monkeypatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "task_sources", Sources())
    plan = {**PLAN, "save_as": ""}
    task = at.get(at.start("packing list", runner(plan), background=False)["id"])
    assert task["status"] == "done" and not (home / "out").exists()


def test_long_write_output_not_truncated_to_3000(home, monkeypatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "task_sources", Sources())
    long = "item\n" * 1500
    run = runner()
    orig = run

    def run2(argv, **kw):
        res = orig(argv, **kw)
        if "PACKING LIST" in res.stdout:
            return subprocess.CompletedProcess(argv, 0, codex_output(long), "")
        return res

    task = at.get(at.start("packing list", run2, background=False)["id"])
    assert len(task["steps"][1]["output"]) > 3000


# ---- task_sources ----------------------------------------------------------


def msg(mid, subject, body, parts=()):
    return {
        "id": mid,
        "payload": {
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": "School <a@school.org>"},
                {"name": "Date", "value": "Mon, 1 Oct 2026"},
            ],
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64(body)}},
                *parts,
            ],
        },
    }


def fake_api(messages, attachments=None, calls=None):
    def api(path, token, params=None):
        if calls is not None:
            calls.append((path, params))
        if path == "/messages":
            return {"messages": [{"id": m["id"]} for m in messages]}
        for m in messages:
            if path == f"/messages/{m['id']}":
                return m
            if path.startswith(f"/messages/{m['id']}/attachments/"):
                return {"data": b64((attachments or {})[path.rsplit("/", 1)[1]])}
        raise AssertionError(path)

    return api


def test_gmail_reads_body_text_attachment_and_linked_doc() -> None:
    doc = "a" * 24
    parts = [
        {
            "filename": "kit.txt",
            "mimeType": "text/plain",
            "body": {"attachmentId": "att1", "size": 10},
        },
        {
            "filename": "big.pdf",
            "mimeType": "application/pdf",
            "body": {"attachmentId": "huge", "size": ts.ATTACHMENT_BYTES + 1},
        },
    ]
    m = msg(
        "m1",
        "Kit list",
        f"See https://docs.google.com/document/d/{doc}/edit and attached",
        parts,
    )
    calls = []
    out = ts.gmail(
        "kit",
        api=fake_api([m], {"att1": "rash vest\nwater shoes"}, calls),
        token="t",
        docs_read=lambda d: f"DOC {d[:3]} contents",
    )
    text = "\n".join(out)
    assert "Subject: Kit list" in text and "docs.google.com" in text
    assert "ATTACHMENT kit.txt" in text and "water shoes" in text
    assert "DOC aaa contents" in text
    assert "big.pdf" not in text  # oversize attachment skipped
    assert all(p.startswith("/messages") for p, _ in calls)  # read-only paths


def test_gmail_attachment_failure_is_contained() -> None:
    parts = [
        {
            "filename": "x.pdf",
            "mimeType": "application/pdf",
            "body": {"attachmentId": "a", "size": 5},
        }
    ]
    out = ts.gmail(
        "q", api=fake_api([msg("m", "S", "b", parts)], {"a": "notpdf"}), token="t"
    )
    assert any("x.pdf" in b and "unreadable" in b for b in out)


def test_gmail_requires_query_and_reports_auth(monkeypatch) -> None:
    with pytest.raises(ts.SourceReadError):
        ts.gmail("  ", api=fake_api([]), token="t")


def test_parse_queries_validates() -> None:
    raw = (
        'noise {"queries": [{"source": "gmail", "query": " a  b "},'
        '{"source": "gmail", "query": "a b"},'
        '{"source": "bash", "query": "ls"},'
        '{"source": "calendar", "days_ahead": "14"},'
        '{"source": "memory", "query": ""}]} tail'
    )
    assert ts.parse_queries(raw) == [
        {"source": "gmail", "query": "a b"},
        {"source": "calendar", "days_ahead": 14, "days_back": 0},
    ]
    assert ts.parse_queries("nope") == []
    many = json.dumps(
        {"queries": [{"source": "gmail", "query": f"q{i}"} for i in range(9)]}
    )
    assert len(ts.parse_queries(many)) == ts.MAX_QUERIES


def test_fetch_isolates_source_failures() -> None:
    def boom(q):
        raise RuntimeError("secret detail")

    text, errors, hits = ts.fetch(
        [
            {"source": "gmail", "query": "x"},
            {"source": "memory", "query": "y"},
            {"source": "calendar", "days_ahead": 3},
        ],
        handlers={
            "gmail": boom,
            "memory": lambda q: ["NOTE a: b"],
            "calendar": lambda a, b: [],
        },
    )
    assert errors == ["gmail: RuntimeError"]  # no raw exception text leaks
    assert hits == 1 and "NOTE a: b" in text and "NO RESULTS" in text


def test_calendar_and_memory_shapes() -> None:
    rows = ts.calendar(
        7,
        lister=lambda lo, hi: [
            {
                "summary": "Trip",
                "start": {"date": "2026-11-01"},
                "end": {"date": "2026-11-06"},
            }
        ],
    )
    assert rows == ["EVENT 2026-11-01 to 2026-11-06: Trip"]
    assert ts.memory("trip", recaller=lambda q, n: [{"source": "s", "text": "t"}]) == [
        "NOTE s: t"
    ]


def test_followup_round_runs_extra_queries(home, monkeypatch) -> None:
    src = Sources()
    monkeypatch.setitem(__import__("sys").modules, "task_sources", src)
    run = runner()
    run.followup = {
        "queries": [
            {"source": "gmail", "query": "kit list"},
            {"source": "gmail", "query": "Chiang Mai"},  # duplicate: dropped
        ]
    }
    task = at.get(at.start("packing list", run, background=False)["id"])
    assert task["status"] == "done"
    assert src.queried[1] == [{"source": "gmail", "query": "kit list"}]
    assert "<<<FIRST RESULTS>>>" in run.calls[2][2]


def test_one_round_switch(home, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_TASK_LOOKUP_ONE_ROUND", "1")
    monkeypatch.setitem(__import__("sys").modules, "task_sources", Sources())
    run = runner()
    task = at.get(at.start("packing list", run, background=False)["id"])
    assert task["status"] == "done" and len(run.calls) == 4


def test_no_match_fails_instead_of_inventing(home, monkeypatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "task_sources", Sources())
    run = runner()
    run.no_match = True
    task = at.get(at.start("packing list", run, background=False)["id"])
    assert task["status"] == "failed"
    assert "not relevant" in task["error"] or "nothing relevant" in task["error"]
    assert not any(c[0].startswith("You write") for c in run.calls)
    assert len(run.calls) == 4  # plan, choose, followup, extract; no write
