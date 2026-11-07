"""Symptom ranking, report building and the new tool surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_rt import ToolError
from system import sysdiag, sysmon
from system.background import BackgroundTools
from system.sysmon import Finding

NEW_TOOLS = (
    "cpu_stats",
    "memory_stats",
    "gpu_stats",
    "temps_stats",
    "disk_stats",
    "network_stats",
    "power_stats",
    "process_stats",
    "full_report",
    "debug_system",
)


def test_new_tools_registered_with_background_tools() -> None:
    ids = [t.id for t in BackgroundTools().tools]
    for name in (*NEW_TOOLS, "usage_stats", "crash_report"):
        assert name in ids
    assert len(ids) == len(set(ids))


def test_symptom_areas_mapping() -> None:
    assert "memory" in sysdiag.symptom_areas("why is memory full")
    assert "network" in sysdiag.symptom_areas("wifi bad")
    fan = sysdiag.symptom_areas("fan loud")
    assert fan["temps"] >= fan["disk"] if "disk" in fan else True
    assert "temps" in fan and "cpu" in fan
    assert "power" in sysdiag.symptom_areas("battery draining")
    assert "process" in sysdiag.symptom_areas("what is eating cpu")
    assert "gpu" in sysdiag.symptom_areas("gpu hot")
    slow = sysdiag.symptom_areas("laptop is slow")
    assert {"cpu", "memory", "disk"} <= set(slow)
    # unknown symptoms still get a sane broad sweep
    assert len(sysdiag.symptom_areas("something weird")) >= 4


def test_rank_causes_prefers_relevant_area_and_severity() -> None:
    findings = [
        Finding("disk", 1, "Disk busy", "nvme0n1 util 95%", score=45),
        Finding(
            "cpu", 2, "ffmpeg hogging CPU", "ffmpeg 900% (threshold 80%)", score=75
        ),
        Finding("network", 2, "Wifi weak", "-85 dBm", score=75),
    ]
    ranked = sysdiag.rank_causes(findings, sysdiag.symptom_areas("what is eating cpu"))
    assert ranked[0].title.startswith("ffmpeg")
    ranked_wifi = sysdiag.rank_causes(findings, sysdiag.symptom_areas("wifi bad"))
    assert ranked_wifi[0].area == "network"


def test_journal_filter_keeps_symptom_lines() -> None:
    lines = [
        "Nov 7 kernel: NVRM: Xid 79 GPU has fallen off the bus",
        "Nov 7 kernel: nvme0n1: I/O error",
        "Nov 7 NetworkManager[1]: wlp62s0: deauthenticated",
        "Nov 7 foo[2]: random",
    ]
    gpu = sysdiag.journal_for_areas(lines, ["gpu"])
    assert len(gpu) == 1 and "NVRM" in gpu[0]
    net = sysdiag.journal_for_areas(lines, ["network"])
    assert "deauthenticated" in net[0]
    both = sysdiag.journal_for_areas(lines, ["disk", "gpu"])
    assert len(both) == 2


def test_verdict_list_and_report_text() -> None:
    findings = [
        Finding("cpu", 1, "CPU busy", "70%", "Close apps", 50),
        Finding("temps", 2, "Package hot", "98C vs 100C", "Check vents", 80),
    ]
    verdicts = sysdiag.verdict_lines(findings)
    assert verdicts[0].startswith("CRITICAL") and "Package hot" in verdicts[0]
    assert sysdiag.verdict_lines([])[0].lower().startswith("nothing abnormal")
    text = sysdiag.build_report_text(
        {"cpu": "cpu detail", "memory": "mem detail"}, findings, header="HDR"
    )
    assert "HDR" in text and "cpu detail" in text and "HEALTH VERDICT" in text


def test_save_report_keeps_last_twenty(tmp_path: Path) -> None:
    for i in range(25):
        p = sysdiag.save_report("x", root=tmp_path, stamp=f"2026-01-01-0000{i:02d}")
    assert p.exists() and p.name.startswith("usage-")
    files = sorted(tmp_path.glob("usage-*.txt"))
    assert len(files) == 20
    assert files[-1].name.endswith("000024.txt")


@pytest.mark.asyncio
async def test_new_tools_refuse_when_not_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    tools = BackgroundTools()
    for name in NEW_TOOLS:
        fn = getattr(BackgroundTools, name)
        with pytest.raises(ToolError, match="only available"):
            if name == "debug_system":
                await fn(tools, None, "laptop is slow")  # type: ignore[arg-type]
            else:
                await fn(tools, None)  # type: ignore[arg-type]


@pytest.fixture
def local(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(sysdiag, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(sysmon, "SAMPLE_WINDOW", 0.0)

    async def missing(*argv: str, timeout: float = 10.0):
        return 127, "", "command not found"

    monkeypatch.setattr(sysmon, "run_cmd", missing)
    monkeypatch.setattr(sysdiag, "run_cmd", missing)
    monkeypatch.setattr("system.sysmon_tools.log_action", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_focused_tools_return_say_and_detail(local: None) -> None:
    tools = BackgroundTools()
    for name in (
        "cpu_stats",
        "memory_stats",
        "gpu_stats",
        "temps_stats",
        "disk_stats",
        "network_stats",
        "power_stats",
    ):
        out = await getattr(tools, name)._func(tools, None)  # type: ignore[attr-defined]
        assert out["say"] and len(out["say"]) <= 400, name
        assert "detail" in out and isinstance(out["detail"], str), name


@pytest.mark.asyncio
async def test_process_stats_options_and_validation(local: None) -> None:
    tools = BackgroundTools()
    fn = tools.process_stats._func  # type: ignore[attr-defined]
    out = await fn(tools, None, sort_by="mem", top=3)
    assert "memory" in out["say"].lower() or "mem" in out["detail"].lower()
    out = await fn(tools, None, name="definitely-not-a-process-xyz")
    assert "no process" in out["say"].lower()
    with pytest.raises(ToolError):
        await fn(tools, None, sort_by="bogus")


@pytest.mark.asyncio
async def test_debug_system_ranks_with_evidence(
    local: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_collect(areas, sample=None):
        return (
            {"cpu": {"cores_count": 12}},
            [
                Finding(
                    "cpu",
                    2,
                    "ffmpeg is hogging the CPU",
                    "ffmpeg 900% vs 80% limit",
                    "Pause it",
                    78,
                ),
                Finding("disk", 1, "Disk busy", "util 91%", "", 45),
            ],
            {"cpu": "detail"},
        )

    monkeypatch.setattr(sysdiag, "collect_areas", fake_collect)

    async def fake_journal(areas):
        return ["kernel: thermal throttled"], [("kernel", 3, "thermal throttled")]

    monkeypatch.setattr(sysdiag, "journal_clusters", fake_journal)
    tools = BackgroundTools()
    out = await tools.debug_system._func(tools, None, "what is eating cpu")  # type: ignore[attr-defined]
    assert "ffmpeg" in out["say"] and len(out["say"]) <= 500
    assert "900%" in out["detail"] and "Pause it" in out["detail"]
    assert "kernel" in out["detail"]  # journal clusters surfaced
    assert "READ-ONLY" in out["detail"].upper() or "suggest" in out["detail"].lower()


@pytest.mark.asyncio
async def test_debug_system_rejects_empty_symptom(local: None) -> None:
    tools = BackgroundTools()
    with pytest.raises(ToolError):
        await tools.debug_system._func(tools, None, "   ")  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_full_report_writes_file(
    local: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sysdiag, "REPORT_DIR", tmp_path)
    tools = BackgroundTools()
    out = await tools.full_report._func(tools, None)  # type: ignore[attr-defined]
    assert Path(out["path"]).exists() and Path(out["path"]).parent == tmp_path
    body = Path(out["path"]).read_text()
    for section in (
        "CPU",
        "MEMORY",
        "GPU",
        "TEMPERATURES",
        "DISK",
        "NETWORK",
        "POWER",
        "PROCESSES",
        "HEALTH VERDICT",
    ):
        assert section in body
    assert len(out["say"]) <= 500


def test_prompt_routes_each_focused_tool() -> None:
    from prompts import AGENT_INSTRUCTIONS

    for name in NEW_TOOLS:
        assert name in AGENT_INSTRUCTIONS, name
    assert "usage_stats" in AGENT_INSTRUCTIONS  # existing routing kept
    assert "never kill or change anything" in AGENT_INSTRUCTIONS


def test_new_tools_are_direct_on_assistant_not_rare() -> None:
    from agent import RARE_SYSTEM_TOOL_IDS, Assistant

    assistant = Assistant(browser=None, llm=None)
    ids = [t.id for t in assistant.tools]
    for name in NEW_TOOLS:
        assert name in ids
        assert name not in RARE_SYSTEM_TOOL_IDS
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_debug_say_includes_current_readings_when_clean(
    local: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_collect(areas, sample=None):
        data = {
            "power": {
                "battery": {"percent": 100, "status": "Full", "watts": 0.0},
                "ac_online": True,
            }
        }
        return data, [], {"power": "x"}

    async def no_journal(areas):
        return [], []

    monkeypatch.setattr(sysdiag, "collect_areas", fake_collect)
    monkeypatch.setattr(sysdiag, "journal_clusters", no_journal)
    tools = BackgroundTools()
    out = await tools.debug_system(None, "battery draining")  # type: ignore[arg-type]
    assert "nothing explains it" in out["say"]
    assert "battery 100 percent full" in out["say"]
