"""Worksheet completion: file search, slot answers, live apply (fake helper)."""

import os

import pytest

from aware import worksheet as ws


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jh"))
    monkeypatch.setattr(ws, "lo_running", lambda: True)


SLOTS = [
    {"id": "T0:B5", "text": "3/4 + 1/6", "where": "table 1, cell B5"},
    {"id": "T0:C5", "text": "4/10 ?", "where": "table 1, cell C5"},
    {"id": "T0:C6", "text": "", "where": "table 1, cell C6"},
]


class Helper:
    def __init__(self, docs=None):
        self.reqs = []
        self.docs = (
            docs
            if docs is not None
            else [
                {
                    "url": "file:///home/s/fractions.odt",
                    "title": "fractions.odt - LibreOffice Writer",
                }
            ]
        )

    def __call__(self, req):
        self.reqs.append(req)
        op = req["op"]
        if op == "docs":
            return {"ok": True, "docs": self.docs}
        if op == "open":
            return {"ok": True, "url": "file://" + req["path"]}
        if op == "slots":
            return {"ok": True, "kind": "writer", "slots": SLOTS}
        if op == "apply":
            return {"ok": True, "applied": len(req["edits"]), "before": {}}
        return {"ok": False}


def test_doc_name_strips_libreoffice_suffix():
    assert ws.doc_name("fractions.odt — LibreOffice Writer") == "fractions.odt"
    assert ws.doc_name("marks.ods - LibreOffice Calc") == "marks.ods"


def test_complete_fills_open_doc_as_one_apply():
    h = Helper()
    answers = lambda slots, ctx: [  # noqa: E731
        {"id": "T0:C5", "text": "11/12"},
        {"id": "T0:C6", "text": "5/2"},
        {"id": "T0:B5", "text": "3/4 + 1/6"},  # unchanged: dropped
        {"id": "nope", "text": "x"},  # unknown: dropped
    ]
    res = ws.complete(
        "fractions.odt - LibreOffice Writer", {}, helper=h, answers=answers
    )
    assert res.ok and res.applied == 2 and "2 answers" in res.spoken
    apply = next(r for r in h.reqs if r["op"] == "apply")
    assert [e["id"] for e in apply["edits"]] == ["T0:C5", "T0:C6"]
    assert not [r for r in h.reqs if r["op"] == "open"]


def test_complete_searches_files_when_not_open(tmp_path):
    h = Helper(docs=[])
    found = str(tmp_path / "fractions.odt")
    res = ws.complete(
        "fractions.odt - LibreOffice Writer",
        {},
        helper=h,
        finder=lambda name: [found],
        answers=lambda s, c: [{"id": "T0:C6", "text": "5/2"}],
    )
    assert res.ok and res.path == found
    assert {"op": "open", "path": found} in h.reqs


def test_complete_not_found_says_so():
    res = ws.complete(
        "lost.odt - LibreOffice Writer", {}, helper=Helper(docs=[]), finder=lambda n: []
    )
    assert not res.ok and res.reason == "not_found" and "lost.odt" in res.spoken


def test_complete_answer_failure_is_spoken_not_raised():
    def boom(slots, ctx):
        raise RuntimeError("429")

    res = ws.complete(
        "fractions.odt - LibreOffice Writer", {}, helper=Helper(), answers=boom
    )
    assert not res.ok and res.reason.startswith("answers:")


def test_find_files_walks_newest_first_and_skips_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(ws, "_allowed", lambda p: True)  # tmp is outside $HOME
    (tmp_path / "a").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "node_modules").mkdir()
    old, new = tmp_path / "a" / "ws.odt", tmp_path / "ws.odt"
    for p in (
        old,
        new,
        tmp_path / ".hidden" / "ws.odt",
        tmp_path / "node_modules" / "ws.odt",
    ):
        p.write_text("x")
    os.utime(old, (1, 1))
    got = ws.find_files("ws.odt - LibreOffice Writer", roots=[tmp_path], recent=[])
    assert got == [str(new), str(old)]


def test_lo_recent_reads_history_urls(tmp_path):
    xcu = tmp_path / "r.xcu"
    xcu.write_text("<value>file:///home/s/My%20Work/ws.odt</value>")
    assert ws.lo_recent(xcu) == ["/home/s/My Work/ws.odt"]


def test_ask_answers_falls_back_across_models():
    import json

    def post(url, body, key):
        if "m1" in url:
            raise RuntimeError("429")
        text = json.dumps({"edits": [{"id": "T0:C5", "text": "11/12"}]})
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}

    got = ws.ask_answers(SLOTS, {}, post=post, key="k", models=["m1", "m2"])
    assert got == [{"id": "T0:C5", "text": "11/12"}]


def test_find_files_applies_secrets_denylist(tmp_path):
    ssh = tmp_path / ".ssh"
    ssh.mkdir()
    (ssh / "ws.odt").write_text("x")
    assert ws.find_files("ws.odt", roots=[tmp_path], recent=[str(ssh / "ws.odt")]) == []
