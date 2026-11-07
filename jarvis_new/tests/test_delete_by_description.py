"""find_and_delete / restore_from_trash: description matching, guards, undo.

Everything runs in a throwaway $HOME with its own $JARVIS_HOME and trash,
so the real home, trash and job records are never touched.
"""

import json
import os
import types
from pathlib import Path

import pytest
from jarvis_rt import ToolError

from system import files_tools
from system.files_tools import FilesTools, _gather_candidates, _rank_candidates


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setenv("JARVIS_HOME", str(h / ".jarvis"))
    monkeypatch.setattr(files_tools, "_brain_paths", lambda phrase: [])
    monkeypatch.setattr(files_tools, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(files_tools, "JARVIS_REPO", h / "repo")
    return h


def _ctx():
    return types.SimpleNamespace(session=None)


def _website(home: Path) -> Path:
    d = home / "jarvis-projects" / "create-a-simple-website-with-a-text-box--11061921"
    d.mkdir(parents=True)
    (d / "index.html").write_text(
        "<html><title>Simple website</title><input type=text><button>Go</button></html>"
    )
    return d


def _write_job(home: Path, job_id: str, task: str, cwd: Path) -> None:
    jobs = home / ".jarvis" / "code-jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / f"{job_id}.json").write_text(
        json.dumps({"id": job_id, "task": task, "cwd": str(cwd)})
    )
    (jobs / f"{job_id}.status.json").write_text(
        json.dumps({"id": job_id, "state": "done", "summary": "made it"})
    )


async def test_single_file_match_is_trashed_and_verified(home):
    f = home / "Downloads" / "quarterly-budget-report.pdf"
    f.parent.mkdir(parents=True)
    f.write_text("numbers")

    out = await FilesTools.find_and_delete(
        FilesTools(), _ctx(), description="the quarterly budget report"
    )

    assert not f.exists()
    assert "trash" in out["say"].lower()
    assert Path(out["trashed"]).read_text() == "numbers"


async def test_single_directory_match_is_trashed_whole(home):
    d = home / "jarvis-projects" / "mega-widget-app"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<title>mega widget</title>")
    (d / "assets" / "a.css").write_text("body{}")

    out = await FilesTools.find_and_delete(
        FilesTools(), _ctx(), description="the mega widget app"
    )

    assert not d.exists()
    trashed = Path(out["trashed"])
    assert (trashed / "assets" / "a.css").read_text() == "body{}"


async def test_ambiguous_match_deletes_nothing(home):
    a = home / "Downloads" / "widget-alpha"
    b = home / "Downloads" / "widget-beta"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    out = await FilesTools.find_and_delete(FilesTools(), _ctx(), description="widget")

    assert a.exists() and b.exists()
    assert "I found 2" in out["say"]
    assert "Nothing's been deleted" in out["say"]
    assert len(out["paths"].split("; ")) == 2


async def test_ambiguous_match_stays_put_even_with_confirm(home):
    a = home / "Downloads" / "widget-alpha"
    b = home / "Downloads" / "widget-beta"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    await FilesTools.find_and_delete(
        FilesTools(), _ctx(), description="widget", confirm=True
    )

    assert a.exists() and b.exists()


async def test_no_match_raises(home):
    with pytest.raises(ToolError, match="can't find"):
        await FilesTools.find_and_delete(
            FilesTools(), _ctx(), description="zzz nonexistent thing"
        )


async def test_refuses_protected_paths(home, tmp_path):
    repo = home / "repo"
    (repo / "sub").mkdir(parents=True)
    (home / ".jarvis" / "brain").mkdir(parents=True)
    (home / ".claude").mkdir()
    (home / ".claude" / "memory.json").write_text("{}")
    (home / "Downloads").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")

    refused = [
        str(home),
        "~",
        str(home / ".jarvis" / "brain"),
        str(home / ".claude" / "memory.json"),
        str(home / ".config"),
        str(repo),
        str(repo / "sub"),
        str(home / "Downloads"),
        str(outside),
        "/etc/passwd",
        "../outside",
        "/",
    ]
    for target in refused:
        with pytest.raises(ToolError):
            await FilesTools.find_and_delete(
                FilesTools(), _ctx(), description="x", path=target
            )

    assert (home / ".claude" / "memory.json").exists()
    assert (outside / "keep.txt").exists()
    assert repo.exists() and (repo / "sub").exists()


async def test_symlink_is_trashed_not_its_target(home, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    link = home / "Downloads" / "shortcut"
    link.parent.mkdir(parents=True)
    os.symlink(outside, link)

    await FilesTools.find_and_delete(FilesTools(), _ctx(), description="", path=str(link))

    assert not os.path.lexists(link)
    assert (outside / "keep.txt").read_text() == "keep"


async def test_big_folder_needs_confirm(home):
    big = home / "Downloads" / "dump"
    big.mkdir(parents=True)
    for i in range(201):
        (big / f"f{i}.txt").write_text("x")

    with pytest.raises(ToolError, match="more than 200"):
        await FilesTools.find_and_delete(
            FilesTools(), _ctx(), description="", path=str(big)
        )
    assert big.exists()

    await FilesTools.find_and_delete(
        FilesTools(), _ctx(), description="", path=str(big), confirm=True
    )
    assert not big.exists()


async def test_restore_round_trip_last_and_by_name(home):
    f = home / "Downloads" / "tax-notes.txt"
    f.parent.mkdir(parents=True)
    f.write_text("receipts")
    g = home / "Downloads" / "holiday-photos-list.txt"
    g.write_text("beach")

    await FilesTools.find_and_delete(FilesTools(), _ctx(), description="", path=str(f))
    await FilesTools.find_and_delete(FilesTools(), _ctx(), description="", path=str(g))
    assert not f.exists() and not g.exists()

    # Blank name undoes the most recent delete.
    out = await FilesTools.restore_from_trash(FilesTools(), _ctx())
    assert g.read_text() == "beach"
    assert "holiday" in out["say"].lower()
    assert not f.exists()

    # A name picks the matching older delete.
    await FilesTools.restore_from_trash(FilesTools(), _ctx(), name="tax notes")
    assert f.read_text() == "receipts"

    with pytest.raises(ToolError, match="Nothing to bring back"):
        await FilesTools.restore_from_trash(FilesTools(), _ctx())


async def test_restore_refuses_to_overwrite(home):
    f = home / "Downloads" / "draft.txt"
    f.parent.mkdir(parents=True)
    f.write_text("old")
    await FilesTools.find_and_delete(FilesTools(), _ctx(), description="", path=str(f))
    f.write_text("new thing put here")

    with pytest.raises(ToolError, match="already"):
        await FilesTools.restore_from_trash(FilesTools(), _ctx())
    assert f.read_text() == "new thing put here"


async def test_job_task_text_resolves_to_job_cwd(home):
    site = _website(home)
    decoy = home / "jarvis-projects" / "code-a-simple-website-with-a-text-box-an"
    decoy.mkdir()
    _write_job(
        home,
        "cc-f5d25208a2",
        "Create a simple website with a text box and button, and open it on my computer.",
        site,
    )

    desc = "simple website text box button"
    ranked = _rank_candidates(_gather_candidates(desc))
    assert ranked[0].path == str(site)
    assert ranked[0].why == "job"

    out = await FilesTools.find_and_delete(FilesTools(), _ctx(), description=desc)

    assert not site.exists()
    assert decoy.exists()  # weaker partial match never gets deleted by mistake
    assert "create-a-simple-website" in out["say"]


async def test_job_cwd_outside_home_is_ignored(home, tmp_path):
    outside = tmp_path / "elsewhere" / "simple-website"
    outside.mkdir(parents=True)
    _write_job(home, "cc-out", "Make a simple website", outside)

    ranked = _rank_candidates(_gather_candidates("simple website"))
    assert all(c.path != str(outside) for c in ranked)
    assert outside.exists()
