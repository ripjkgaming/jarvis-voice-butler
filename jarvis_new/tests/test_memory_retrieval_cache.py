"""Recall reuses unchanged note parsing without hiding vault edits."""

import os
from pathlib import Path

import pytest

import memory


@pytest.fixture
def vault(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_VAULT", str(tmp_path))
    directory = tmp_path / "Memory" / "Threads"
    directory.mkdir(parents=True)
    return directory


def recall(query="physics"):
    return memory.recall(query, brain=lambda _q, _n: [])


def count_reads(monkeypatch):
    original = Path.read_text
    seen = []

    def read(path, *args, **kwargs):
        seen.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    return seen


def test_unchanged_note_is_read_and_parsed_only_once(vault, monkeypatch):
    note = vault / "physics.md"
    note.write_text("# Physics\n\nWave speed equals frequency times wavelength.\n")
    reads = count_reads(monkeypatch)
    first = recall()
    assert first and recall() == first
    assert reads == [note]


def test_edit_invalidates_even_when_size_and_mtime_are_preserved(vault):
    note = vault / "lesson.md"
    note.write_text("physics lesson")
    assert recall()
    previous = note.stat()
    note.write_text("history lesson")
    os.utime(note, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert recall() == []
    assert recall("history")


def test_deleted_or_archived_note_is_not_recalled(vault):
    note = vault / "physics.md"
    note.write_text("physics lesson")
    assert recall()
    note.rename(vault.parent.parent / "archived.md")
    assert recall() == []


def test_replacement_file_with_preserved_mtime_is_fresh(vault):
    note = vault / "lesson.md"
    note.write_text("physics lesson")
    assert recall()
    previous = note.stat()
    replacement = vault / "replacement.tmp"
    replacement.write_text("history lesson")
    os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    replacement.replace(note)
    assert recall() == []
    assert recall("history")


def test_large_notes_do_not_occupy_cache(vault, monkeypatch):
    note = vault / "physics.md"
    note.write_text("physics " * 100_000)
    reads = count_reads(monkeypatch)
    assert recall()
    assert recall()
    assert reads == [note, note]


def test_cache_retention_is_bounded_across_large_vault(vault, monkeypatch):
    from collections import OrderedDict

    monkeypatch.setattr(memory, "_note_cache", OrderedDict())
    for index in range(80):
        (vault / f"lesson-{index}.md").write_text("physics lesson\n" * 1000)
    assert recall()
    assert len(memory._note_cache) <= memory._NOTE_CACHE_FILES
    assert (
        sum(entry[2] for entry in memory._note_cache.values())
        <= memory._NOTE_CACHE_CHARS
    )
