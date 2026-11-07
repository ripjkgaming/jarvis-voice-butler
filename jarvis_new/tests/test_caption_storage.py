"""Caption storage must remain cheap and race-free under concurrent updates."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import hud_events


def test_hud_reader_does_not_scan_old_history(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    path = tmp_path / "captions.log"
    path.write_text(("1\tsir\t" + "x" * 500 + "\n") * 5000 + "2\tjarvis\tLatest ✓\n")
    original = Path.open
    reads = []

    class Counted:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, *args):
            value = self.stream.read(*args)
            reads.append(len(value))
            return value

    def counted(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        return Counted(stream) if self == path else stream

    monkeypatch.setattr(Path, "open", counted)
    assert hud_events.read_captions(1)[0]["text"] == "Latest ✓"
    assert sum(reads) <= 4096


def test_corrupt_history_does_not_break_caption_write(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "captions.log").write_bytes(b"1\tsir\tbad \xff\n")
    assert hud_events.caption("jarvis", "Still running")
    assert hud_events.read_captions(1)[0]["text"] == "Still running"


def test_live_caption_writers_use_independent_atomic_files(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    barrier = Barrier(2)
    original = Path.replace

    def synchronized_replace(self, target):
        barrier.wait(timeout=5)
        return original(self, target)

    monkeypatch.setattr(Path, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda i: hud_events.live_caption(str(i), f"line {i}"), range(2))
        )
    assert results == [True, True]
    assert not list(tmp_path.glob("*.tmp"))


def test_caption_prune_preserves_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    path = tmp_path / "captions.log"
    path.write_text("".join(f"{i}\tsir\tline {i}\n" for i in range(10000)))
    assert hud_events.caption("jarvis", "newest")
    lines = path.read_text().splitlines()
    assert len(lines) == hud_events.CAPTIONS_KEEP
    assert hud_events.read_captions(1)[0]["text"] == "newest"


def test_concurrent_appends_survive_pruning(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(hud_events, "_active_speaker", lambda: "jarvis")
    path = tmp_path / "captions.log"
    path.write_text("1\tsir\told\n" * 250)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda i: hud_events.caption("sir", f"new {i}"), range(100))
        )
    assert all(results)
    captions = hud_events.read_captions(250)
    assert {f"new {i}" for i in range(100)} <= {row["text"] for row in captions}
    assert len(path.read_text().splitlines()) <= 250


def test_failed_atomic_write_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert hud_events.live_caption("previous", "keep me")
    previous = (tmp_path / "caption_live.json").read_bytes()

    def fail_replace(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(Path, "replace", fail_replace)
    assert not hud_events.live_caption("next", "new line")
    assert (tmp_path / "caption_live.json").read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))
