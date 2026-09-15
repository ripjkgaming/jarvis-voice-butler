import pytest

import briefing_job


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
