"""Detailed-reading support: per-process tables, groups and extra parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from system import sysdiag, sysmon

# ---------------------------------------------------------------- parsers


def test_cpu_breakdown_all_fields() -> None:
    a = sysmon.parse_cpu_fields(
        "cpu  100 10 100 700 50 5 20 15 0 0\ncpu0 1 1 1 1 1 1 1 1\n"
    )
    b = sysmon.parse_cpu_fields(
        "cpu  200 20 150 800 60 15 40 25 0 0\ncpu0 2 2 2 2 2 2 2 2\n"
    )
    br = sysmon.cpu_breakdown(a["cpu"], b["cpu"])
    # delta total = 100+10+50+100+10+10+20+10 = 310
    assert br["user"] == pytest.approx(100 * 100 / 310, abs=0.01)
    assert br["system"] == pytest.approx(100 * 50 / 310, abs=0.01)
    assert br["iowait"] == pytest.approx(100 * 10 / 310, abs=0.01)
    assert br["steal"] == pytest.approx(100 * 10 / 310, abs=0.01)
    assert set(br) == {
        "user",
        "nice",
        "system",
        "idle",
        "iowait",
        "irq",
        "softirq",
        "steal",
    }
    assert sum(br.values()) == pytest.approx(100.0, abs=0.01)


def test_parse_stat_totals_for_rates() -> None:
    t = sysmon.parse_stat_counters(
        "intr 5000 1 2\nprocesses 900\nctxt 77\nsoftirq 300 1 2\n"
    )
    assert t == {"intr": 5000, "forks": 900, "ctxt": 77, "softirq": 300}


def test_parse_status_and_smaps_rollup() -> None:
    st = sysmon.parse_status_extra(
        "Name:\tbrave\nThreads:\t22\nVmSwap:\t  2048 kB\nRssAnon:\t 1000 kB\n"
        "RssFile:\t 500 kB\nRssShmem:\t 100 kB\nvoluntary_ctxt_switches:\t10\n"
        "nonvoluntary_ctxt_switches:\t5\n"
    )
    assert st["swap_kb"] == 2048 and st["rss_shmem_kb"] == 100 and st["ctx"] == 15
    sm = sysmon.parse_smaps_rollup(
        "Rss: 4000 kB\nPss: 2500 kB\nShared_Clean: 1000 kB\nShared_Dirty: 200 kB\nSwap: 7 kB\nPss_Anon: 900 kB\n"
    )
    assert sm == {"pss_kb": 2500, "shared_kb": 1200, "swap_kb": 7, "pss_anon_kb": 900}
    assert sysmon.parse_status_extra("") == {}
    assert sysmon.parse_smaps_rollup("") == {}


def test_parse_fdinfo_and_client_rates() -> None:
    txt = (
        "pos:\t0\ndrm-driver:\ti915\ndrm-client-id:\t42\n"
        "drm-engine-render:\t1000000000 ns\ndrm-engine-video:\t0 ns\n"
        "drm-total-system:\t2048 KiB\ndrm-total-local0:\t1024 KiB\n"
    )
    c = sysmon.parse_fdinfo(txt)
    assert c["driver"] == "i915" and c["client"] == 42
    assert c["engines"] == {"render": 1_000_000_000, "video": 0}
    assert c["mem_kb"] == 3072
    amd = sysmon.parse_fdinfo(
        "drm-driver:\tamdgpu\ndrm-client-id:\t7\ndrm-engine-gfx:\t5 ns\ndrm-memory-vram:\t4096 KiB\ndrm-memory-gtt:\t100 KiB\n"
    )
    assert amd["mem_kb"] == 4196
    assert sysmon.parse_fdinfo("nothing here") == {}
    before = {(10, 42): {**c, "pid": 10}}
    after = {
        (10, 42): {**c, "pid": 10, "engines": {"render": 1_500_000_000, "video": 0}}
    }
    rows = sysmon.drm_client_rates(before, after, dt=1.0)
    assert rows[0]["pid"] == 10 and rows[0]["util_pct"] == pytest.approx(50.0)
    assert rows[0]["engine"] == "render"


def test_parse_pmon() -> None:
    out = (
        "# gpu         pid   type     sm    mem    enc    dec    jpg    ofa    command \n"
        "# Idx           #    C/G      %      %      %      %      %      %    name \n"
        "    0       4197   C+G      5     44      0      0      -      -    kwin_wayland   \n"
        "    0       5000      G      -      -      -      -      -      -    brave   \n"
    )
    rows = sysmon.parse_pmon(out)
    assert rows[0] == {
        "pid": 4197, "type": "C+G", "sm": 5, "mem": 44, "enc": 0, "dec": 0,
        "name": "kwin_wayland",
    }  # fmt: skip
    assert rows[1]["sm"] is None and rows[1]["name"] == "brave"
    assert sysmon.parse_pmon("") == []


def test_parse_ss_conns_and_rates_per_process() -> None:
    t0 = (
        'ESTAB 0 0 10.0.0.2:5000 1.1.1.1:443 users:(("brave",pid=5,fd=9))\n'
        "\t cubic bytes_sent:1000 bytes_received:9000\n"
        'ESTAB 0 0 10.0.0.2:5001 1.1.1.2:443 users:(("brave",pid=5,fd=10))\n'
        "\t cubic bytes_sent:10 bytes_received:90\n"
        'ESTAB 0 0 10.0.0.2:5002 9.9.9.9:80 users:(("curl",pid=6,fd=3))\n'
        "\t cubic bytes_sent:1 bytes_received:1\n"
    )
    t1 = t0.replace(
        "bytes_sent:1000 bytes_received:9000", "bytes_sent:3000 bytes_received:19000"
    )
    c0, c1 = sysmon.parse_ss_conns(t0), sysmon.parse_ss_conns(t1)
    assert len(c0) == 3 and c0[0]["proc"] == "brave" and c0[0]["pid"] == 5
    assert c0[0]["peer"] == "1.1.1.1:443" and c0[0]["local"] == "10.0.0.2:5000"
    per = sysmon.net_proc_rates(c0, c1, dt=2.0)
    brave = next(p for p in per if p["proc"] == "brave")
    assert brave["conns"] == 2
    assert brave["tx_bps"] == pytest.approx(1000.0)  # 2000 bytes / 2 s
    assert brave["rx_bps"] == pytest.approx(5000.0)
    assert brave["total_bytes"] == 3000 + 19000 + 10 + 90
    assert per[0]["proc"] == "brave"  # ranked by current rate


def test_parse_snmp_and_sockstat() -> None:
    snmp = (
        "Tcp: RtoAlgorithm ActiveOpens OutSegs RetransSegs InErrs\n"
        "Tcp: 1 100 10000 150 3\n"
        "Udp: InDatagrams InErrors\nUdp: 50 2\n"
    )
    p = sysmon.parse_snmp(snmp)
    assert p["Tcp"]["RetransSegs"] == 150 and p["Udp"]["InErrors"] == 2
    ss = "sockets: used 1200\nTCP: inuse 30 orphan 0 tw 5 alloc 40 mem 3\nUDP: inuse 9 mem 1\n"
    s = sysmon.parse_sockstat(ss)
    assert s["TCP"]["tw"] == 5 and s["sockets"]["used"] == 1200


def test_proc_rates_adds_wakeups_and_io() -> None:
    def rec(ticks, ctx, rb):
        return {
            "pid": 1, "comm": "x", "state": "S", "ticks": ticks, "threads": 1,
            "rss_kb": 1, "start_ticks": 0, "uid": 1000, "ctx": ctx,
            "read_bytes": rb, "write_bytes": rb,
        }  # fmt: skip

    rows = sysmon.proc_rates(
        {1: rec(0, 100, 0)}, {1: rec(50, 400, 2_000_000)}, dt=2.0, clk_tck=100
    )
    assert rows[0]["ctx_ps"] == pytest.approx(150.0)
    assert rows[0]["read_bps"] == pytest.approx(1_000_000)


def test_estimate_process_watts_shares_package_power() -> None:
    rows = [{"cpu_pct": 300.0}, {"cpu_pct": 100.0}, {"cpu_pct": 0.0}]
    sysmon.estimate_process_watts(rows, package_w=20.0)
    assert rows[0]["est_w"] == pytest.approx(15.0)
    assert rows[1]["est_w"] == pytest.approx(5.0)
    rows2 = [{"cpu_pct": 10.0}]
    sysmon.estimate_process_watts(rows2, package_w=None)
    assert rows2[0]["est_w"] is None


def test_group_procs_keeps_members_for_individual_listing() -> None:
    rows = [
        {
            "name": "brave",
            "pid": i,
            "cpu_pct": float(i),
            "rss_kb": 10 * i,
            "read_bps": None,
            "write_bps": None,
        }
        for i in range(1, 9)
    ]
    g = sysmon.group_procs(rows, "cpu_pct")[0]
    assert g["count"] == 8 and len(g["members"]) <= 6
    assert g["members"][0]["pid"] == 8  # busiest first


def test_fmt_proc_line_cpu_mem_io_power() -> None:
    row = {
        "pid": 970320, "name": "brave", "user": "ripjk", "cpu_pct": 6.04,
        "threads": 22, "state": "S", "age_s": 3.7 * 3600, "rss_kb": 274 * 1024,
        "pss_kb": 200 * 1024, "shared_kb": 50 * 1024, "swap_kb": 1024,
        "read_bps": 2_000_000.0, "write_bps": 10_000.0, "ctx_ps": 12.0,
        "est_w": 1.25, "cmd": "/opt/brave/brave --profile x",
    }  # fmt: skip
    cpu = sysmon.fmt_proc_line(row, "cpu")
    for needle in (
        "970320",
        "ripjk",
        "6.0%",
        "threads 22",
        "state S",
        "3.7h",
        "brave --profile x",
    ):
        assert needle in cpu, needle
    mem = sysmon.fmt_proc_line(row, "mem", total_kb=16 * 1024 * 1024)
    for needle in ("rss 274 MB", "pss 200 MB", "shared 50 MB", "swap 1 MB", "1.7%"):
        assert needle in mem, needle
    io = sysmon.fmt_proc_line(row, "io")
    assert "read 2.0 MB/s" in io and "write 10 kB/s" in io
    pw = sysmon.fmt_proc_line(row, "power")
    assert "~1.25 W" in pw and "12 wakeups/s" in pw
    assert "n/a" in sysmon.fmt_proc_line(
        {**row, "read_bps": None, "write_bps": None}, "io"
    )


def test_render_process_section_has_groups_and_individuals() -> None:
    members = [
        {
            "pid": 2,
            "name": "brave",
            "user": "u",
            "cpu_pct": 30.0,
            "threads": 1,
            "state": "S",
            "age_s": 60.0,
            "rss_kb": 10,
            "cmd": "brave --type=gpu",
        },
        {
            "pid": 3,
            "name": "brave",
            "user": "u",
            "cpu_pct": 10.0,
            "threads": 1,
            "state": "S",
            "age_s": 60.0,
            "rss_kb": 10,
            "cmd": "brave --type=renderer",
        },
    ]
    groups = [
        {
            "name": "brave",
            "cpu_pct": 40.0,
            "rss_kb": 20,
            "count": 2,
            "io_bps": 0.0,
            "pids": [2, 3],
            "members": members,
            "threads": 2,
        }
    ]
    text = sysmon.render_process_section("CPU", members, groups, "cpu")
    assert "Top 2 processes by CPU" in text
    assert (
        "Program groups" in text
        and "brave: 2 processes" in text
        and "total 40.0% cpu" in text
    )
    assert text.count("--type=gpu") >= 2  # individual table AND under its group


def test_heaters_for_sensor() -> None:
    cpu = [{"name": "ffmpeg", "cpu_pct": 300.0}]
    gpu = [{"name": "blender", "mem_mb": 900}]
    io = [{"name": "rsync", "io_bps": 5e7}]
    assert "ffmpeg" in sysmon.heaters_for_sensor("coretemp:Package id 0", cpu, gpu, io)
    assert "blender" in sysmon.heaters_for_sensor("nvidia:RTX", cpu, gpu, io)
    assert "rsync" in sysmon.heaters_for_sensor("nvme0:Composite", cpu, gpu, io)
    assert (
        "network"
        in sysmon.heaters_for_sensor("mt7921_phy0:temp1", cpu, gpu, io).lower()
    )


def test_read_thermal_exposes_all_trips_and_cooling(tmp_path: Path) -> None:
    tz = tmp_path / "t" / "thermal_zone0"
    tz.mkdir(parents=True)
    (tz / "type").write_text("acpitz\n")
    (tz / "temp").write_text("50000\n")
    for i, (k, v) in enumerate([("passive", 95000), ("critical", 110000)]):
        (tz / f"trip_point_{i}_type").write_text(k + "\n")
        (tz / f"trip_point_{i}_temp").write_text(f"{v}\n")
    cd = tmp_path / "t" / "cooling_device0"
    cd.mkdir()
    (cd / "type").write_text("Fan\n")
    (cd / "cur_state").write_text("0\n")
    (cd / "max_state").write_text("4\n")
    d = sysmon.read_thermal(tmp_path / "t", tmp_path / "h")
    assert d["trips"] == [
        {"zone": "acpitz", "trips": [("passive", 95.0), ("critical", 110.0)]}
    ]
    assert d["cooling_all"] == [{"type": "Fan", "state": 0, "max": 4}]


# ---------------------------------------------------------------- collectors


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysmon, "SAMPLE_WINDOW", 0.0)


async def _missing(*argv: str, timeout: float = 10.0):
    return 127, "", "command not found"


@pytest.mark.asyncio
async def test_every_detail_has_process_breakdown_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real /proc, no external commands: each detail carries its process table."""
    monkeypatch.setattr(sysmon, "run_cmd", _missing)
    expect = {
        "cpu": ("processes by CPU", "threads", "state", "run", "cmd:"),
        "memory": (
            "processes by memory",
            "rss",
            "pss",
            "shared",
            "swap",
            "Program groups",
        ),
        "gpu": ("GPU processes",),
        "temps": ("Likely heat sources",),
        "disk": ("processes by disk IO", "Mounts:"),
        "network": ("Per-interface",),
        "power": ("processes by estimated power", "wakeups/s"),
    }
    for area, needles in expect.items():
        data = await sysmon.safe_collect(area, sysmon.COLLECTORS[area])
        assert "error" not in data, (area, data.get("error"))
        _say, detail = sysmon.RENDERERS[area](data, sysmon.FINDERS[area](data))
        for n in needles:
            assert n.lower() in detail.lower(), (area, n)
    cpu = await sysmon.collect_cpu()
    detail = sysmon.render_cpu(cpu, [])[1]
    assert "Program groups" in detail
    assert "state " in detail and "threads " in detail and "cmd:" in detail


@pytest.mark.asyncio
async def test_detail_is_complete_but_say_is_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sysmon, "run_cmd", _missing)
    for area in ("cpu", "memory", "disk", "power", "temps"):
        data = await sysmon.COLLECTORS[area]()
        say, detail = sysmon.RENDERERS[area](data, sysmon.FINDERS[area](data))
        assert len(say) <= 400
        assert len(detail) > len(say) * 2, area


@pytest.mark.asyncio
async def test_memory_detail_lists_every_meminfo_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sysmon, "run_cmd", _missing)
    data = await sysmon.collect_memory()
    detail = sysmon.render_memory(data, [])[1]
    assert "Full /proc/meminfo" in detail
    for field in ("MemTotal", "MemAvailable", "Active(anon)", "Committed_AS"):
        assert field in detail, field


@pytest.mark.asyncio
async def test_focused_tool_saves_detail_to_report_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from system.background import BackgroundTools

    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(sysmon, "run_cmd", _missing)
    monkeypatch.setattr(sysdiag, "REPORT_DIR", tmp_path)
    monkeypatch.setattr("system.sysmon_tools.log_action", lambda *a, **k: None)
    tools = BackgroundTools()
    out = await tools.cpu_stats(None)  # type: ignore[arg-type]
    assert len(out["say"]) <= 400
    path = Path(out["path"])
    assert path.parent == tmp_path and path.name.startswith("cpu-")
    assert path.read_text().strip() == out["detail"].strip()
    assert out["top"] and len(out["top"].splitlines()) <= 12
    # rotation is per area: 25 saves keep 20
    for i in range(25):
        sysdiag.save_report(
            "x", root=tmp_path, stamp=f"2026-01-01-0000{i:02d}", prefix="cpu"
        )
    assert len(list(tmp_path.glob("cpu-*.txt"))) == 20


def test_prompt_detailed_reading_routing() -> None:
    from prompts import AGENT_INSTRUCTIONS

    assert "detailed reading" in AGENT_INSTRUCTIONS
    assert "`top`" in AGENT_INSTRUCTIONS and "`path`" in AGENT_INSTRUCTIONS
