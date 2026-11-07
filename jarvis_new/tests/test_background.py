import pytest
from jarvis_rt import ToolError

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


def test_format_usage_is_detailed() -> None:
    from system.background import format_usage, parse_df, parse_ps

    snap = {
        "load1": 4.0,
        "load5": 3.0,
        "load15": 2.0,
        "cores": 8,
        "mem_total_gb": 16.0,
        "mem_used_gb": 8.0,
        "swap_total_gb": 4.0,
        "swap_used_gb": 1.0,
        "battery": 80,
        "charging": 0,
        "temp_c": 61.0,
    }
    ps = "COMMAND %CPU %MEM\npython 50.0 5.0\npython 20.0 3.0\nbrave 30.0 10.0\n"
    procs = parse_ps(ps)
    assert procs[0] == ("python", 70.0, 8.0)
    say = format_usage(
        uptime="up 2 hours",
        snap=snap,
        procs=procs,
        disk=parse_df(
            "Filesystem Size Used Avail Use% Mounted\n/dev/x 100G 40G 60G 40% /"
        ),
        ai="AI today: 1,000 tokens, about $0.50.",
    )
    for needle in (
        "8 cores",
        "50 percent busy",
        "8.0 of 16.0 GB",
        "swap",
        "40G of 100G",
        "battery 80 percent, on battery",
        "61 degrees",
        "python 70% CPU",
        "AI today",
    ):
        assert needle in say
