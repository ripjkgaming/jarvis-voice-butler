"""Fixture-driven tests for the focused system monitors (system.sysmon)."""

from __future__ import annotations

from pathlib import Path

import pytest

from system import sysmon

STAT_A = """cpu  100 0 100 700 50 0 50 0 0 0
cpu0 50 0 50 350 25 0 25 0 0 0
cpu1 50 0 50 350 25 0 25 0 0 0
intr 1
ctxt 5000
btime 1
processes 1
procs_running 3
procs_blocked 1
"""
STAT_B = """cpu  200 0 200 800 50 0 50 0 0 0
cpu0 150 0 100 350 25 0 25 0 0 0
cpu1 50 0 100 450 25 0 25 0 0 0
intr 1
ctxt 9000
btime 1
processes 1
procs_running 2
procs_blocked 0
"""

MEMINFO = """MemTotal:       16000000 kB
MemFree:         2000000 kB
MemAvailable:    8000000 kB
Buffers:          200000 kB
Cached:          4000000 kB
SwapTotal:       4000000 kB
SwapFree:        3000000 kB
Slab:             600000 kB
SReclaimable:     400000 kB
Dirty:               120 kB
"""

PSI = """some avg10=13.05 avg60=5.79 avg300=4.30 total=3259824539
full avg10=1.00 avg60=0.00 avg300=0.00 total=0
"""

# ---- parsers -----------------------------------------------------------


def test_parse_proc_stat_and_utilisation() -> None:
    a, b = sysmon.parse_proc_stat(STAT_A), sysmon.parse_proc_stat(STAT_B)
    assert a["ctxt"] == 5000 and a["procs_running"] == 3 and a["procs_blocked"] == 1
    util = sysmon.cpu_utilisation(a, b)
    assert util["total"] == pytest.approx(66.7, abs=0.1)
    assert len(util["cores"]) == 2
    assert util["cores"][0] == pytest.approx(100.0, abs=0.1)
    assert util["cores"][1] == pytest.approx(33.3, abs=0.1)
    assert util["iowait"] == pytest.approx(0.0)


def test_cpu_utilisation_handles_identical_samples() -> None:
    a = sysmon.parse_proc_stat(STAT_A)
    util = sysmon.cpu_utilisation(a, a)
    assert util["total"] == 0.0 and util["cores"] == [0.0, 0.0]


def test_parse_meminfo_and_memory_summary() -> None:
    mem = sysmon.parse_meminfo(MEMINFO)
    assert mem["MemTotal"] == 16000000
    summ = sysmon.memory_summary(mem)
    assert summ["used_gb"] == pytest.approx((16000000 - 8000000) / 1048576)
    assert summ["avail_pct"] == pytest.approx(50.0)
    assert summ["swap_used_gb"] == pytest.approx(1000000 / 1048576)


def test_parse_psi() -> None:
    psi = sysmon.parse_psi(PSI)
    assert psi["some"]["avg10"] == 13.05
    assert psi["full"]["avg10"] == 1.0
    assert sysmon.parse_psi("") == {}


def test_parse_swaps_and_zram() -> None:
    swaps = sysmon.parse_swaps(
        "Filename Type Size Used Priority\n/dev/zram0 partition 8000000 100000 100\n"
    )
    assert swaps == [{"name": "/dev/zram0", "size_kb": 8000000, "used_kb": 100000}]
    z = sysmon.parse_zram_mm_stat("4194304 1048576 1100000 0 1100000 0 0 0 0", 8 << 30)
    assert z["orig_mb"] == 4.0 and z["ratio"] == 4.0 and z["disk_gb"] == 8.0


def test_parse_vmstat_and_oom_lines() -> None:
    vm = sysmon.parse_vmstat("oom_kill 2\npswpin 10\npswpout 20\nfoo x\n")
    assert vm["oom_kill"] == 2 and vm["pswpout"] == 20
    text = (
        "Nov 05 10:00:00 host kernel: Out of memory: Killed process 4242 (chrome) "
        "total-vm:1kB\n"
        "Nov 06 11:00:00 host systemd-oomd[1]: Killed /user.slice/app.slice/brave.scope "
        "due to memory pressure\n"
        "Nov 06 12:00:00 host kernel: unrelated line\n"
    )
    kills = sysmon.parse_oom_journal(text)
    assert len(kills) == 2
    assert "chrome" in kills[0] and "brave.scope" in kills[1]


def test_parse_pid_stat_handles_spaces_in_comm() -> None:
    line = (
        "321 (Isolated Web Co) S 1 321 321 0 -1 4194560 100 0 0 0 "
        "700 300 0 0 20 0 9 0 5000 123456789 2500 0 0\n"
    )
    rec = sysmon.parse_pid_stat(line)
    assert rec is not None
    assert rec["pid"] == 321 and rec["comm"] == "Isolated Web Co"
    assert rec["state"] == "S" and rec["ticks"] == 1000
    assert rec["threads"] == 9 and rec["start_ticks"] == 5000
    assert rec["rss_kb"] == 2500 * sysmon.PAGE_KB
    assert sysmon.parse_pid_stat("garbage") is None


def _proc(pid: int, comm: str, ticks: int, rss: int, **kw) -> dict:
    base = {
        "pid": pid,
        "comm": comm,
        "state": "S",
        "ticks": ticks,
        "threads": 1,
        "rss_kb": rss,
        "start_ticks": 0,
        "uid": 1000,
        "read_bytes": None,
        "write_bytes": None,
    }
    base.update(kw)
    return base


def test_proc_rates_and_grouping() -> None:
    before = {1: _proc(1, "brave", 0, 1000), 2: _proc(2, "brave", 0, 500)}
    after = {
        1: _proc(1, "brave", 100, 1100, read_bytes=0, write_bytes=0),
        2: _proc(2, "brave", 50, 500),
        3: _proc(3, "new", 10, 10),
    }
    rows = sysmon.proc_rates(before, after, dt=1.0, clk_tck=100)
    by_pid = {r["pid"]: r for r in rows}
    assert by_pid[1]["cpu_pct"] == pytest.approx(100.0)
    assert by_pid[2]["cpu_pct"] == pytest.approx(50.0)
    assert by_pid[3]["cpu_pct"] == 0.0  # no baseline -> no rate
    groups = sysmon.group_procs(rows, "cpu_pct")
    assert groups[0]["name"] == "brave" and groups[0]["cpu_pct"] == pytest.approx(150)
    assert groups[0]["count"] == 2 and groups[0]["rss_kb"] == 1600


def test_short_cmdline_prefers_script_name() -> None:
    assert sysmon.short_cmdline("/usr/bin/python3\0/opt/x/agent.py\0--dev\0") == (
        "python3 agent.py"
    )
    assert sysmon.short_cmdline("") == ""


def test_parse_nvidia_xml() -> None:
    xml = """<nvidia_smi_log><driver_version>595.84</driver_version>
<gpu id="0"><product_name>NVIDIA GeForce RTX 2050</product_name>
<fan_speed>N/A</fan_speed><performance_state>P5</performance_state>
<clocks_event_reasons>
<clocks_event_reason_gpu_idle>Active</clocks_event_reason_gpu_idle>
<clocks_event_reason_sw_thermal_slowdown>Active</clocks_event_reason_sw_thermal_slowdown>
<clocks_event_reason_sw_power_cap>Not Active</clocks_event_reason_sw_power_cap>
</clocks_event_reasons>
<fb_memory_usage><total>4096 MiB</total><used>1024 MiB</used></fb_memory_usage>
<utilization><gpu_util>37 %</gpu_util><memory_util>12 %</memory_util></utilization>
<temperature><gpu_temp>58 C</gpu_temp><gpu_temp_slow_threshold>97 C</gpu_temp_slow_threshold></temperature>
<gpu_power_readings><instant_power_draw>6.06 W</instant_power_draw><current_power_limit>51.83 W</current_power_limit></gpu_power_readings>
<clocks><graphics_clock>210 MHz</graphics_clock><mem_clock>810 MHz</mem_clock></clocks>
<max_clocks><graphics_clock>2100 MHz</graphics_clock></max_clocks>
<processes><process_info><pid>4197</pid><type>C+G</type>
<process_name>/usr/bin/kwin_wayland</process_name><used_memory>6 MiB</used_memory></process_info></processes>
</gpu></nvidia_smi_log>"""
    gpus = sysmon.parse_nvidia_xml(xml)
    assert len(gpus) == 1
    g = gpus[0]
    assert g["vendor"] == "NVIDIA" and g["name"].endswith("RTX 2050")
    assert (
        g["util_pct"] == 37 and g["vram_used_mb"] == 1024 and g["vram_total_mb"] == 4096
    )
    assert g["temp_c"] == 58 and g["temp_slow_c"] == 97 and g["fan_pct"] is None
    assert g["power_w"] == pytest.approx(6.06) and g["power_limit_w"] == pytest.approx(
        51.83
    )
    assert g["clock_mhz"] == 210 and g["clock_max_mhz"] == 2100
    assert g["throttle"] == ["sw_thermal_slowdown"]  # idle is not throttling
    assert g["procs"] == [
        {"pid": 4197, "name": "kwin_wayland", "type": "C+G", "mem_mb": 6}
    ]
    assert sysmon.parse_nvidia_xml("not xml") == []


def test_read_drm_gpus_amd_and_intel(tmp_path: Path) -> None:
    amd = tmp_path / "card0" / "device"
    (amd / "hwmon" / "hwmon3").mkdir(parents=True)
    (amd / "vendor").write_text("0x1002\n")
    (amd / "gpu_busy_percent").write_text("42\n")
    (amd / "mem_info_vram_used").write_text(str(2 * 1024**3))
    (amd / "mem_info_vram_total").write_text(str(8 * 1024**3))
    hw = amd / "hwmon" / "hwmon3"
    (hw / "temp1_input").write_text("71000\n")
    (hw / "power1_average").write_text("35000000\n")
    (hw / "fan1_input").write_text("1800\n")
    (hw / "freq1_input").write_text("1500000000\n")
    intel = tmp_path / "card1"
    (intel / "device").mkdir(parents=True)
    (intel / "device" / "vendor").write_text("0x8086\n")
    (intel / "gt_cur_freq_mhz").write_text("500\n")
    (intel / "gt_max_freq_mhz").write_text("1100\n")
    (tmp_path / "card1-eDP-1").mkdir()  # connector dirs ignored
    gpus = sysmon.read_drm_gpus(tmp_path)
    kinds = {g["vendor"]: g for g in gpus}
    assert kinds["AMD"]["util_pct"] == 42 and kinds["AMD"]["temp_c"] == 71
    assert kinds["AMD"]["vram_used_mb"] == 2048 and kinds["AMD"]["fan_rpm"] == 1800
    assert kinds["AMD"]["power_w"] == pytest.approx(35.0)
    assert kinds["AMD"]["clock_mhz"] == 1500
    assert (
        kinds["Intel"]["clock_mhz"] == 500 and kinds["Intel"]["clock_max_mhz"] == 1100
    )
    assert kinds["Intel"]["util_pct"] is None


def test_read_thermal_labels_thresholds_and_junk(tmp_path: Path) -> None:
    tz = tmp_path / "thermal" / "thermal_zone0"
    tz.mkdir(parents=True)
    (tz / "type").write_text("x86_pkg_temp\n")
    (tz / "temp").write_text("72000\n")
    (tz / "trip_point_0_type").write_text("critical\n")
    (tz / "trip_point_0_temp").write_text("100000\n")
    (tz / "trip_point_1_type").write_text("passive\n")
    (tz / "trip_point_1_temp").write_text("95000\n")
    cd = tmp_path / "thermal" / "cooling_device0"
    cd.mkdir()
    (cd / "type").write_text("Processor\n")
    (cd / "cur_state").write_text("2\n")
    (cd / "max_state").write_text("3\n")
    hw = tmp_path / "hwmon" / "hwmon1"
    hw.mkdir(parents=True)
    (hw / "name").write_text("nvme\n")
    (hw / "temp1_input").write_text("44000\n")
    (hw / "temp1_label").write_text("Composite\n")
    (hw / "temp1_crit").write_text("84800\n")
    (hw / "temp2_input").write_text("45000\n")
    (hw / "temp2_label").write_text("Sensor 1\n")
    (hw / "temp2_max").write_text("65261800\n")  # junk threshold, must be dropped
    (hw / "fan1_input").write_text("3200\n")
    data = sysmon.read_thermal(tmp_path / "thermal", tmp_path / "hwmon")
    names = {s["label"]: s for s in data["sensors"]}
    assert names["thermal:x86_pkg_temp"]["temp_c"] == 72.0
    assert names["thermal:x86_pkg_temp"]["crit_c"] == 100.0
    assert names["thermal:x86_pkg_temp"]["high_c"] == 95.0
    assert names["nvme:Composite"]["crit_c"] == pytest.approx(84.8)
    assert names["nvme:Sensor 1"]["high_c"] is None
    assert data["max"]["label"] == "thermal:x86_pkg_temp"
    assert data["fans"] == [{"label": "nvme:fan1", "rpm": 3200}]
    assert data["cooling_active"] == [{"type": "Processor", "state": 2, "max": 3}]


def test_parse_diskstats_and_rates() -> None:
    a = "259 0 nvme0n1 100 0 2000 50 10 0 400 20 0 100 80\n259 1 nvme0n1p1 1 0 8 0 0 0 0 0 0 0 0\n 7 0 loop0 1 0 2 0 0 0 0 0 0 0 0\n"
    b = "259 0 nvme0n1 300 0 6000 250 20 0 1400 120 1 600 480\n259 1 nvme0n1p1 1 0 8 0 0 0 0 0 0 0 0\n 7 0 loop0 1 0 2 0 0 0 0 0 0 0 0\n"
    da = sysmon.parse_diskstats(a, whole_devices={"nvme0n1"})
    assert set(da) == {"nvme0n1"}
    rates = sysmon.disk_rates(da, sysmon.parse_diskstats(b, {"nvme0n1"}), dt=1.0)
    r = rates["nvme0n1"]
    assert r["read_mbps"] == pytest.approx(4000 * 512 / 1e6)
    assert r["write_mbps"] == pytest.approx(1000 * 512 / 1e6)
    assert r["iops"] == pytest.approx(210)
    assert r["util_pct"] == pytest.approx(50.0)
    assert r["await_ms"] == pytest.approx((200 + 100) / 210, abs=0.01)


def test_parse_mounts_filters_pseudo_fs() -> None:
    text = (
        "/dev/nvme0n1p3 / btrfs rw,relatime 0 0\n"
        "proc /proc proc rw 0 0\n"
        "tmpfs /run tmpfs rw 0 0\n"
        "/dev/nvme0n1p3 /home btrfs rw,subvol=/home 0 0\n"
        "/dev/sda1 /mnt/data ext4 ro,relatime 0 0\n"
        "/dev/loop3 /snap/x squashfs ro 0 0\n"
    )
    mounts = sysmon.parse_mounts(text)
    assert [m["mount"] for m in mounts] == ["/", "/home", "/mnt/data"]
    assert mounts[2]["ro"] is True and mounts[0]["ro"] is False


def test_mount_usage_real_directory(tmp_path: Path) -> None:
    use = sysmon.mount_usage(str(tmp_path))
    assert use is not None
    assert use["total_gb"] > 0 and 0 <= use["used_pct"] <= 100
    assert sysmon.mount_usage("/definitely/not/here") is None


def test_parse_smart_health() -> None:
    assert (
        sysmon.parse_smart_health(
            "SMART overall-health self-assessment test result: PASSED"
        )
        == "PASSED"
    )
    assert sysmon.parse_smart_health("SMART Health Status: FAILED!") == "FAILED"
    assert (
        sysmon.parse_smart_health(
            "Smartctl open device: /dev/nvme0 failed: Permission denied"
        )
        == "needs root"
    )
    assert sysmon.parse_smart_health("") == "unknown"


NETDEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 5000 50 0 0 0 0 0 0 5000 50 0 0 0 0 0 0
wlp62s0: 1000000 1000 2 20 0 0 0 0 500000 500 1 0 0 0 0 0
"""


def test_parse_net_dev_and_rates() -> None:
    a = sysmon.parse_net_dev(NETDEV)
    assert a["wlp62s0"]["rx_bytes"] == 1000000 and a["wlp62s0"]["rx_drop"] == 20
    b = {k: dict(v) for k, v in a.items()}
    b["wlp62s0"]["rx_bytes"] += 2_000_000
    b["wlp62s0"]["tx_bytes"] += 500_000
    rates = sysmon.net_rates(a, b, dt=1.0)
    assert rates["wlp62s0"]["rx_mbps"] == pytest.approx(16.0)
    assert rates["wlp62s0"]["tx_kbps"] == pytest.approx(4000.0)


def test_parse_wireless_iw_and_gateway_ping() -> None:
    wl = (
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
        "wlp62s0: 0000   60.  -50.  -256        0      0      0      0      0        0\n"
    )
    assert sysmon.parse_proc_wireless(wl) == {
        "wlp62s0": {"quality": 60.0, "level_dbm": -50.0}
    }
    iw = (
        "Connected to aa:bb (on wlp62s0)\n\tSSID: KAAK_5G\n\tfreq: 5180\n"
        "\tsignal: -48 dBm\n\trx bitrate: 866.7 MBit/s\n\ttx bitrate: 780.0 MBit/s\n"
    )
    link = sysmon.parse_iw_link(iw)
    assert link["ssid"] == "KAAK_5G" and link["signal_dbm"] == -48
    assert link["tx_mbit"] == 780.0 and link["freq_mhz"] == 5180
    assert sysmon.parse_iw_link("Not connected.") == {}
    route = "Iface\tDestination\tGateway\tFlags\nwlp62s0\t00000000\t0100A8C0\t0003\n"
    assert sysmon.parse_default_gateway(route) == "192.168.0.1"
    ping = "3 packets transmitted, 3 received, 0% packet loss, time 400ms\nrtt min/avg/max/mdev = 2.1/3.5/6.0/1.0 ms\n"
    assert sysmon.parse_ping(ping) == {"loss_pct": 0.0, "avg_ms": 3.5, "max_ms": 6.0}
    assert sysmon.parse_ping("") == {}


def test_parse_ss_listeners_and_talkers() -> None:
    listen = (
        "tcp LISTEN 0 4096 127.0.0.1:6060 0.0.0.0:*\n"
        'tcp LISTEN 0 5 0.0.0.0:4000 0.0.0.0:* users:(("python",pid=987,fd=3))\n'
        "udp UNCONN 0 0 0.0.0.0:68 0.0.0.0:*\n"
    )
    ports = sysmon.parse_ss_listeners(listen)
    assert len(ports) == 3 and ports[1]["proc"] == "python" and ports[1]["port"] == 4000
    talk = (
        'ESTAB 0 0 10.0.0.2:5000 1.1.1.1:443 users:(("brave",pid=5,fd=9))\n'
        "\t cubic wscale:7,7 bytes_sent:1000 bytes_acked:1000 bytes_received:9000 segs_out:5\n"
        'ESTAB 0 0 10.0.0.2:5001 1.1.1.2:443 users:(("brave",pid=5,fd=10))\n'
        "\t cubic bytes_sent:10 bytes_acked:10 bytes_received:90\n"
        "ESTAB 0 0 10.0.0.2:5002 1.1.1.3:443\n"
        "\t cubic bytes_sent:5 bytes_received:5\n"
    )
    top = sysmon.parse_ss_talkers(talk)
    assert (
        top[0]["proc"] == "brave" and top[0]["bytes"] == 10100 and top[0]["conns"] == 2
    )
    assert any(t["proc"] == "unknown" for t in top)


def test_read_battery_and_time_remaining(tmp_path: Path) -> None:
    bat = tmp_path / "BAT1"
    bat.mkdir()
    for k, v in {
        "capacity": "50",
        "status": "Discharging",
        "charge_now": "1500000",
        "charge_full": "3000000",
        "charge_full_design": "4000000",
        "voltage_now": "15000000",
        "current_now": "1000000",
        "cycle_count": "300",
        "technology": "Li-ion",
    }.items():
        (bat / k).write_text(v + "\n")
    ac = tmp_path / "ACAD"
    ac.mkdir()
    (ac / "type").write_text("Mains\n")
    (ac / "online").write_text("0\n")
    info = sysmon.read_power(tmp_path)
    b = info["battery"]
    assert b["percent"] == 50 and b["status"] == "Discharging"
    assert b["watts"] == pytest.approx(15.0)  # 1 A at 15 V
    assert b["health_pct"] == pytest.approx(75.0)
    assert b["cycles"] == 300
    assert b["hours_left"] == pytest.approx(1.5 * 15 / 15.0)  # 22.5 Wh / 15 W
    assert info["ac_online"] is False


def test_read_power_without_battery(tmp_path: Path) -> None:
    info = sysmon.read_power(tmp_path)
    assert info["battery"] is None and info["ac_online"] is None


# ---- findings ------------------------------------------------------------


def test_cpu_findings_flag_hog_and_pressure() -> None:
    data = {
        "total_pct": 92.0,
        "iowait": 25.0,
        "steal": 0.0,
        "cores_count": 12,
        "load": (20.0, 10.0, 5.0),
        "psi": {"some": {"avg10": 45.0}},
        "freq": {"avg_mhz": 1000, "max_mhz": 4000},
        "groups": [{"name": "ffmpeg", "cpu_pct": 900.0, "count": 1, "rss_kb": 1}],
        "ctx_per_s": 1000,
    }
    f = sysmon.cpu_findings(data)
    titles = " | ".join(x.title + x.evidence for x in f)
    assert "ffmpeg" in titles and "900" in titles
    assert any(x.severity == 2 for x in f)
    assert any("iowait" in (x.title + x.evidence).lower() for x in f)
    assert any("throttl" in (x.title + x.evidence).lower() for x in f)
    assert (
        sysmon.cpu_findings(
            {"total_pct": 5, "cores_count": 12, "load": (0.1, 0.1, 0.1), "groups": []}
        )
        == []
    )


def test_memory_findings_oom_swap_and_pressure() -> None:
    data = {
        "avail_pct": 4.0,
        "swap_used_pct": 80.0,
        "psi": {"some": {"avg10": 30.0}},
        "oom_kills": ["Nov 5 Killed process 1 (chrome)"],
        "swap_activity": {"pswpin_s": 500, "pswpout_s": 500},
        "groups": [{"name": "brave", "rss_kb": 6_000_000, "count": 20, "cpu_pct": 1}],
        "total_kb": 16_000_000,
        "slab_pct": 2.0,
    }
    f = sysmon.memory_findings(data)
    text = " ".join(x.title + " " + x.evidence for x in f)
    assert "brave" in text and "OOM" in text and "swap" in text.lower()
    assert max(x.severity for x in f) == 2
    assert (
        sysmon.memory_findings(
            {"avail_pct": 60.0, "swap_used_pct": 0, "groups": [], "total_kb": 1}
        )
        == []
    )


def test_temps_gpu_disk_net_power_findings() -> None:
    hot = {
        "sensors": [
            {
                "label": "thermal:x86_pkg_temp",
                "temp_c": 98.0,
                "crit_c": 100.0,
                "high_c": None,
            }
        ],
        "fans": [],
        "cooling_active": [],
    }
    assert any(
        x.severity >= 1 and "98" in x.evidence for x in sysmon.temps_findings(hot)
    )
    cool = {
        "sensors": [{"label": "a", "temp_c": 40.0, "crit_c": 100.0, "high_c": None}],
        "fans": [],
        "cooling_active": [],
    }
    assert sysmon.temps_findings(cool) == []

    gpu = {
        "gpus": [
            {
                "vendor": "NVIDIA",
                "name": "RTX",
                "temp_c": 90,
                "temp_slow_c": 97,
                "vram_used_mb": 3900,
                "vram_total_mb": 4096,
                "util_pct": 99,
                "throttle": ["hw_thermal_slowdown"],
                "procs": [],
            }
        ]
    }
    gf = " ".join(x.title for x in sysmon.gpu_findings(gpu))
    assert "VRAM" in gf and "throttl" in gf.lower()

    disk = {
        "mounts": [
            {"mount": "/", "used_pct": 96.0, "inode_pct": 10.0, "ro": False},
            {"mount": "/mnt/x", "used_pct": 10.0, "inode_pct": 95.0, "ro": True},
        ],
        "io": {
            "nvme0n1": {
                "util_pct": 99.0,
                "await_ms": 80.0,
                "read_mbps": 1,
                "write_mbps": 1,
                "iops": 1,
            }
        },
        "smart": {"nvme0n1": "FAILED"},
    }
    df = sysmon.disk_findings(disk)
    assert len(df) >= 5 and max(x.severity for x in df) == 2

    net = {
        "ifaces": [
            {
                "name": "wlp1",
                "wifi": {"signal_dbm": -82},
                "rx_pkts": 1000,
                "rx_drop": 100,
                "rx_err": 0,
                "tx_err": 0,
                "tx_pkts": 1,
            }
        ],
        "gateway": {"loss_pct": 20.0, "avg_ms": 300.0},
    }
    nf = " ".join(x.title + x.evidence for x in sysmon.network_findings(net))
    assert "-82" in nf and "loss" in nf.lower()

    pw = {
        "battery": {
            "percent": 12,
            "status": "Discharging",
            "watts": 35.0,
            "health_pct": 60.0,
            "cycles": 900,
            "hours_left": 0.3,
        },
        "ac_online": False,
        "profile": "performance",
    }
    pf = " ".join(x.title + x.evidence for x in sysmon.power_findings(pw))
    assert "35" in pf and "health" in pf.lower() and "12" in pf


def test_process_findings_zombies_and_dstate() -> None:
    data = {"total": 500, "zombies": 12, "dstate": 5, "groups": [], "by_user": {}}
    titles = " ".join(x.title for x in sysmon.process_findings(data))
    assert "zombie" in titles.lower() and "uninterruptible" in titles.lower()


# ---- renderers ----------------------------------------------------------


def test_render_functions_keep_say_short() -> None:
    cpu = {
        "total_pct": 23.0,
        "cores_pct": [10.0] * 12,
        "cores_count": 12,
        "load": (2.1, 1.5, 1.0),
        "freq": {
            "avg_mhz": 3400,
            "max_mhz": 4600,
            "min_mhz": 400,
            "cur_mhz": [3400] * 12,
        },
        "governor": "powersave",
        "groups": [
            {"name": f"p{i}", "cpu_pct": 50.0 - i, "count": 1, "rss_kb": 1}
            for i in range(8)
        ],
        "procs": [],
        "ctx_per_s": 12000,
        "running": 3,
        "blocked": 0,
        "throttle": {"core": 0, "package": 0},
        "rapl_w": 14.2,
        "psi": {},
        "iowait": 0.0,
        "steal": 0.0,
        "notes": [],
    }
    say, detail = sysmon.render_cpu(cpu, [])
    assert len(say) <= 400 and "23" in say and "12 cores" in say
    assert "governor" in detail.lower() and "context" in detail.lower()


def test_render_gpu_says_plainly_when_missing() -> None:
    say, detail = sysmon.render_gpu(
        {"gpus": [], "notes": ["nvidia-smi not installed"]}, []
    )
    assert "no gpu" in say.lower()
    assert "nvidia-smi" in detail


# ---- fail-soft collectors -----------------------------------------------


@pytest.fixture(autouse=True)
def _fast_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sysmon, "SAMPLE_WINDOW", 0.0)


async def _missing(*argv: str, timeout: float = 10.0):
    return 127, "", f"command not found: {argv[0]}"


@pytest.mark.asyncio
async def test_collectors_fail_soft_without_files_or_commands(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sysmon, "run_cmd", _missing)
    monkeypatch.setattr(sysmon, "DRM_ROOT", tmp_path / "drm")
    monkeypatch.setattr(sysmon, "THERMAL_ROOT", tmp_path / "thermal")
    monkeypatch.setattr(sysmon, "HWMON_ROOT", tmp_path / "hwmon")
    monkeypatch.setattr(sysmon, "POWER_ROOT", tmp_path / "power")
    monkeypatch.setattr(sysmon, "PROC", tmp_path / "proc")
    monkeypatch.setattr(sysmon, "SYS_CPU", tmp_path / "cpu")
    monkeypatch.setattr(sysmon, "NET_ROOT", tmp_path / "net")
    monkeypatch.setattr(sysmon, "SYS_BLOCK", tmp_path / "block")
    monkeypatch.setattr(sysmon, "MOUNTS_FILE", tmp_path / "mounts")
    for name in (
        "cpu",
        "memory",
        "gpu",
        "temps",
        "disk",
        "network",
        "power",
        "process",
    ):
        data = await sysmon.COLLECTORS[name]()
        assert isinstance(data, dict), name
        say, detail = sysmon.RENDERERS[name](data, sysmon.FINDERS[name](data))
        assert isinstance(say, str) and say, name
        assert len(say) <= 400, (name, say)
        assert isinstance(detail, str), name
    gpu = await sysmon.collect_gpu()
    assert gpu["gpus"] == []
    assert "no gpu" in sysmon.render_gpu(gpu, [])[0].lower()


@pytest.mark.asyncio
async def test_safe_collect_swallows_exceptions() -> None:
    async def boom() -> dict:
        raise RuntimeError("kaput")

    data = await sysmon.safe_collect("cpu", boom)
    assert data["error"].startswith("RuntimeError")
    say, _detail = sysmon.RENDERERS["cpu"](data, [])
    assert "unavailable" in say.lower()


@pytest.mark.asyncio
async def test_live_collectors_smoke() -> None:
    """Real /proc on this machine: must work or degrade, never raise."""
    for name in ("cpu", "memory", "temps", "disk", "network", "power", "process"):
        data = await sysmon.safe_collect(name, sysmon.COLLECTORS[name])
        assert isinstance(data, dict)
    cpu = await sysmon.collect_cpu()
    assert cpu["cores_count"] >= 1 and 0 <= cpu["total_pct"] <= 100
    mem = await sysmon.collect_memory()
    assert mem["total_kb"] > 0
