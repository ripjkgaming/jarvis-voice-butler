import pytest

import briefing_job
from briefing_job import shape_briefing_for_speech


@pytest.mark.asyncio
async def test_deliver_falls_back_to_log_without_notify(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(briefing_job, "BRIEFING_LOG", tmp_path / "briefings.log")

    def _missing(*args, **kwargs):
        raise FileNotFoundError("no notify-send")

    monkeypatch.setattr(briefing_job.subprocess, "run", _missing)
    assert briefing_job.deliver("test briefing") == "log"
    assert "test briefing" in (tmp_path / "briefings.log").read_text()


@pytest.mark.asyncio
async def test_build_briefing_uses_inbox_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")

    async def _fake_briefing(self, context):
        return {"say": "fake morning bundle"}

    monkeypatch.setattr("system.inbox.InboxTools.morning_briefing", _fake_briefing)
    assert await briefing_job.build_briefing() == "fake morning bundle"


# --- Briefing delivery discipline (Phase 5 slice 5.2) ---

BUNDLE = (
    "London: 14 degrees and light rain. "
    "Headlines: council approves night buses; river festival opens Friday. "
    "School: 2026-09-21: 09:00 Maths; 11:00 Physics. "
    "Todos: 1. buy milk; 2. call mom."
)


def test_shape_keeps_signal_drops_empty_sections() -> None:
    say = BUNDLE + " No classes on Sunday. Nothing to brief otherwise."
    shaped = shape_briefing_for_speech(say)
    assert "London" in shaped and "Maths" in shaped and "milk" in shaped
    assert "No classes" not in shaped and "Nothing to brief" not in shaped


def test_shape_caps_600_at_sentence_boundary() -> None:
    long_bundle = " ".join(f"Signal sentence number {i}." for i in range(100))
    shaped = shape_briefing_for_speech(long_bundle)
    assert len(shaped) <= briefing_job.SPOKEN_CAP_CHARS
    assert shaped.endswith(".")
    assert "number 0." in shaped  # bundle order kept, head first


def test_shape_empty_returns_empty() -> None:
    assert shape_briefing_for_speech("Nothing to brief this morning.") == ""
    assert shape_briefing_for_speech("Weather is unavailable right now.") == ""
    assert shape_briefing_for_speech("") == ""


@pytest.mark.asyncio
async def test_main_dedupes_repeats(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(briefing_job, "BRIEFING_STATE", tmp_path / "state.json")
    monkeypatch.setattr(briefing_job, "BRIEFING_LOG", tmp_path / "briefings.log")

    async def _bundle() -> str:
        return BUNDLE

    monkeypatch.setattr(briefing_job, "build_briefing", _bundle)
    monkeypatch.setattr(briefing_job.subprocess, "run", lambda *a, **k: None)
    assert await briefing_job.main() == "notification+log"
    assert await briefing_job.main() == "skipped-duplicate"


@pytest.mark.asyncio
async def test_main_skips_empty_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(briefing_job, "BRIEFING_STATE", tmp_path / "state.json")

    async def _empty() -> str:
        return "Nothing to brief this morning."

    monkeypatch.setattr(briefing_job, "build_briefing", _empty)
    assert await briefing_job.main() == "skipped-empty"


def test_deliver_logs_full_bundle(tmp_path, monkeypatch) -> None:
    log = tmp_path / "briefings.log"
    monkeypatch.setattr(briefing_job, "BRIEFING_LOG", log)
    monkeypatch.setattr(briefing_job.subprocess, "run", lambda *a, **k: None)
    assert briefing_job.deliver("short shaped", full_text=BUNDLE) == "notification+log"
    assert BUNDLE in log.read_text()
