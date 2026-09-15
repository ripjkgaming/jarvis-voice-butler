import pytest
from livekit.agents.llm import ToolError

from system.background import BackgroundTools, cluster_journal


def test_cluster_journal_groups_by_unit() -> None:
    lines = [
        "Sep 14 kernel: usb disconnect",
        "Sep 14 kernel: usb reset",
        "Sep 14 gnome-shell[123]: clutter failed",
    ]
    clusters = cluster_journal(lines)
    assert clusters[0][0] == "kernel" and clusters[0][1] == 2


def test_background_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in BackgroundTools().tools]
    for expected in (
        "usage_stats",
        "crash_report",
        "webcam_snapshot",
        "idle_status",
    ):
        assert expected in ids


@pytest.mark.asyncio
async def test_background_tools_refuse_when_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    with pytest.raises(ToolError, match="only available"):
        await BackgroundTools.usage_stats(BackgroundTools(), None)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="only available"):
        await BackgroundTools.crash_report(BackgroundTools(), None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_crash_report_no_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")

    async def fake_run(*argv: str, timeout: float = 10.0):
        return 0, "", ""

    monkeypatch.setattr("system.background.run_cmd", fake_run)
    result = await BackgroundTools.crash_report(BackgroundTools(), None)  # type: ignore[arg-type]
    assert "No errors" in result["say"]


@pytest.mark.asyncio
async def test_crash_report_clusters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")

    async def fake_run(*argv: str, timeout: float = 10.0):
        out = "Sep 14 kernel: oops\nSep 14 kernel: oops2\nSep 14 foo[1]: bar\n"
        return 0, out, ""

    monkeypatch.setattr("system.background.run_cmd", fake_run)
    result = await BackgroundTools.crash_report(BackgroundTools(), None)  # type: ignore[arg-type]
    assert "3 errors" in result["say"] and "kernel" in result["say"]


@pytest.mark.asyncio
async def test_crash_report_rejects_bad_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    with pytest.raises(ToolError, match="Odd time range"):
        await BackgroundTools.crash_report(
            BackgroundTools(),
            None,
            since="1 hour ago; rm -rf /",  # type: ignore[arg-type]
        )
