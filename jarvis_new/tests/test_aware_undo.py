"""Always Aware undo journal (PLAN.md §5): backups, diff log, hash-checked undo.

Everything runs in tmp_path with JARVIS_HOME pointed there; no real files.
"""

import json
import os
import time

import pytest

from aware import undo_journal as uj


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jhome"))
    return tmp_path


def _w(journal, path, data: bytes):
    """Journaled write the way fileedit does it."""
    journal.backup(path)
    before = path.read_bytes() if path.exists() else None
    uj.atomic_write(path, data)
    journal.record_write(path, before=before, after=data)


def test_create_and_open_round_trip(tmp_path):
    j = uj.Journal.create("run-1", kind="file", title="tidy")
    assert j.dir.is_dir()
    assert oct(j.dir.stat().st_mode & 0o777) == "0o700"
    again = uj.Journal.open("run-1")
    assert again.meta()["kind"] == "file"
    assert again.meta()["status"] == "open"
    with pytest.raises(ValueError):
        uj.Journal.create("../evil", kind="file", title="x")


def test_backup_before_first_write_only(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("one")
    j = uj.Journal.create("r", kind="file", title="t")
    _w(j, f, b"two")
    _w(j, f, b"three")
    backups = list((j.dir / "files").iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"one"
    ops = [op for op in j.ops() if op["op"] == "write"]
    assert len(ops) == 1  # coalesced
    assert ops[0]["sha256_before"] == uj.sha256_bytes(b"one")
    assert ops[0]["sha256_after"] == uj.sha256_bytes(b"three")
    patch = (j.dir / "diff.patch").read_text()
    assert "-one" in patch and "+two" in patch and "+three" in patch


def test_undo_restores_original_and_removes_created(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("orig")
    os.chmod(f, 0o640)
    new = tmp_path / "new.txt"
    j = uj.Journal.create("r", kind="file", title="t")
    _w(j, f, b"changed")
    _w(j, new, b"fresh")
    j.set_status("verified")
    rep = uj.undo(j)
    assert f.read_text() == "orig"
    assert oct(f.stat().st_mode & 0o777) == "0o640"
    assert not new.exists()
    assert sorted(rep.restored) == sorted([str(f), str(new)])
    assert rep.left_alone == []
    assert j.meta()["undone"] is True


def test_undo_leaves_file_sir_edited_since(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("orig")
    g = tmp_path / "b.txt"
    g.write_text("orig-b")
    j = uj.Journal.create("r", kind="file", title="t")
    _w(j, f, b"mine")
    _w(j, g, b"mine-b")
    f.write_text("sir typed this")  # edited after the run
    rep = uj.undo(j)
    assert f.read_text() == "sir typed this"
    assert g.read_text() == "orig-b"
    assert rep.left_alone == [str(f)]


def test_move_and_delete_undo(tmp_path):
    src = tmp_path / "in" / "x.pdf"
    src.parent.mkdir()
    src.write_bytes(b"pdf")
    dst = tmp_path / "out" / "x.pdf"
    dst.parent.mkdir()
    gone = tmp_path / "old.log"
    gone.write_text("log")
    j = uj.Journal.create("r", kind="file", title="t")
    j.move(src, dst)
    assert dst.read_bytes() == b"pdf" and not src.exists()
    trash = j.trash(gone)
    assert not gone.exists() and trash.read_text() == "log"
    assert trash.is_relative_to(j.dir / "trash")
    rep = uj.undo(j)
    assert src.read_bytes() == b"pdf" and not dst.exists()
    assert gone.read_text() == "log"
    assert len(rep.restored) == 2


def test_move_undo_skips_when_moved_file_changed(tmp_path):
    src = tmp_path / "x.txt"
    src.write_text("a")
    dst = tmp_path / "y.txt"
    j = uj.Journal.create("r", kind="file", title="t")
    j.move(src, dst)
    dst.write_text("edited")
    rep = uj.undo(j)
    assert dst.read_text() == "edited" and not src.exists()
    assert rep.left_alone == [str(dst)]


def test_move_refuses_collision(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.write_text("a")
    b.write_text("b")
    j = uj.Journal.create("r", kind="file", title="t")
    with pytest.raises(FileExistsError):
        j.move(a, b)
    assert b.read_text() == "b"


def test_undo_reverse_order_write_then_move(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("v1")
    b = tmp_path / "b.txt"
    j = uj.Journal.create("r", kind="file", title="t")
    _w(j, a, b"v2")
    j.move(a, b)
    uj.undo(j)
    assert a.read_text() == "v1" and not b.exists()


def test_record_absent_after_for_code_deletes(tmp_path):
    f = tmp_path / "gone.py"
    j = uj.Journal.create("r", kind="code", title="t")
    j.record_external(f, before=b"print(1)\n", after_sha=None, mode=0o644)
    rep = uj.undo(j)
    assert f.read_text() == "print(1)\n"
    assert rep.restored == [str(f)]


def test_atomic_write_refuses_symlink_target(tmp_path):
    real = tmp_path / "real"
    real.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(OSError):
        uj.atomic_write(link, b"x")
    assert real.read_text() == "keep"
    assert uj.sha256_file(link) is None  # never follows links


def test_atomic_write_preserves_mode(tmp_path):
    f = tmp_path / "s.sh"
    f.write_text("#!/bin/sh\n")
    os.chmod(f, 0o755)
    uj.atomic_write(f, b"#!/bin/sh\necho\n")
    assert oct(f.stat().st_mode & 0o777) == "0o755"
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_backup_size_cap(tmp_path):
    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 100)
    j = uj.Journal.create("r", kind="file", title="t")
    with pytest.raises(uj.TooLarge):
        j.backup(f, max_bytes=10)


def test_runs_newest_first_and_undo_last(tmp_path):
    files = []
    for i in range(3):
        f = tmp_path / f"f{i}"
        f.write_text("o")
        j = uj.Journal.create(f"run-{i}", kind="file", title=str(i), now=1000 + i)
        _w(j, f, b"n")
        j.set_status("verified")
        files.append(f)
    assert [j.run_id for j in uj.runs()] == ["run-2", "run-1", "run-0"]
    reps = uj.undo_last(2)
    assert [r.run_id for r in reps] == ["run-2", "run-1"]
    assert [f.read_text() for f in files] == ["n", "o", "o"]
    reps = uj.undo_last(1)  # already-undone runs are skipped
    assert [r.run_id for r in reps] == ["run-0"]


def test_undo_refuses_open_run(tmp_path):
    f = tmp_path / "f"
    f.write_text("o")
    j = uj.Journal.create("r", kind="file", title="t")
    _w(j, f, b"n")
    assert uj.undo_last(1) == []  # still running: not undoable
    rep = uj.undo(j)  # takeover's own revert path works on an open run
    assert f.read_text() == "o" and rep.restored


def test_prune_by_age_and_size(tmp_path):
    now = time.time()
    old = uj.Journal.create("old", kind="file", title="t", now=now - 15 * 86400)
    old.set_status("verified")
    mid = uj.Journal.create("mid", kind="file", title="t", now=now - 2 * 86400)
    mid.set_status("verified")
    (mid.dir / "files").mkdir(exist_ok=True)
    (mid.dir / "files" / "0").write_bytes(b"x" * 500)
    new = uj.Journal.create("new", kind="file", title="t", now=now)
    new.set_status("verified")
    (new.dir / "files").mkdir(exist_ok=True)
    (new.dir / "files" / "0").write_bytes(b"y" * 500)
    pruned = uj.prune(now=now, max_bytes=800)
    assert set(pruned) == {"old", "mid"}
    assert [j.run_id for j in uj.runs()] == ["new"]


def test_manifest_is_private_jsonl(tmp_path):
    f = tmp_path / "f"
    f.write_text("o")
    j = uj.Journal.create("r", kind="file", title="t")
    _w(j, f, b"n")
    man = j.dir / "manifest.jsonl"
    assert oct(man.stat().st_mode & 0o777) == "0o600"
    for line in man.read_text().splitlines():
        json.loads(line)


def test_events_log(tmp_path):
    j = uj.Journal.create("r", kind="code", title="t")
    j.event("progress", "reading tests")
    j.event("blocked", "needs a password")
    ev = j.events()
    assert [e["kind"] for e in ev] == ["progress", "blocked"]
