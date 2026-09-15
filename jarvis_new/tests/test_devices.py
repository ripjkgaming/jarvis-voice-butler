import pytest
from livekit.agents.llm import ToolError

from system.devices import (
    DeviceTools,
    logical_monitors,
    match_bluetooth_device,
    parse_kb_color,
    parse_monitor_outputs,
    resolve_monitor_spec,
)


def test_parse_kb_color_names_and_hex() -> None:
    assert parse_kb_color("static red") == (255, 0, 0)
    assert parse_kb_color("make it #00ff00") == (0, 255, 0)
    assert parse_kb_color("polychromatic") is None


def test_match_bluetooth_device_fuzzy_and_mac() -> None:
    devs = [
        ("AA:BB:CC:DD:EE:FF", "Sony Headphones"),
        ("11:22:33:44:55:66", "Logi Mouse"),
    ]
    assert match_bluetooth_device("sony", devs) == (
        "AA:BB:CC:DD:EE:FF",
        "Sony Headphones",
    )
    assert match_bluetooth_device("aa:bb:cc:dd:ee:ff", devs)[1] == "Sony Headphones"
    assert match_bluetooth_device("unknown thing xyz", devs) is None
    assert match_bluetooth_device("", devs) is None


def test_parse_monitor_outputs_strips_ansi() -> None:
    out = "\x1b[32mOutput: 1 DP-1\x1b[0m\nOutput: 2 eDP-1\n"
    assert parse_monitor_outputs(out) == ["DP-1", "eDP-1"]


def test_logical_monitors_m1_m2_m3() -> None:
    m = logical_monitors(["DP-1", "HDMI-A-2", "eDP-1"])
    assert m["M1"] == "DP-1"
    assert m["M2"] == "eDP-1"
    assert m["M3"] == "HDMI-A-2"


def test_logical_monitors_laptop_only() -> None:
    m = logical_monitors(["eDP-1"])
    assert m["M1"] == "eDP-1" and m["M2"] == "eDP-1" and m["M3"] == "eDP-1"


def test_resolve_monitor_spec_aliases_and_fallback() -> None:
    mapping = {"M1": "DP-1", "M2": "eDP-1", "M3": "HDMI-A-2"}
    r = resolve_monitor_spec("laptop", mapping, ["DP-1", "eDP-1"])
    assert r["name"] == "eDP-1" and not r["fallback"]
    r = resolve_monitor_spec("monitor 3", mapping, ["DP-1"])
    assert r["name"] == "DP-1" and r["fallback"]
    r = resolve_monitor_spec("DP-1", mapping, ["DP-1", "eDP-1"])
    assert r["name"] == "DP-1" and not r["fallback"]


def test_device_tools_register_expected_ids() -> None:
    ids = [tool.id for tool in DeviceTools().tools]
    for expected in (
        "wifi_control",
        "bluetooth_control",
        "audio_output",
        "keyboard_light",
        "monitor_setup",
        "open_on_monitor",
        "usb_devices",
        "phone_devices",
        "home_control",
        "play_game",
    ):
        assert expected in ids


@pytest.mark.asyncio
async def test_device_tools_refuse_when_not_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_LOCAL", raising=False)
    tools = DeviceTools()
    with pytest.raises(ToolError, match="only available"):
        await DeviceTools.wifi_control(tools, None)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="only available"):
        await DeviceTools.usb_devices(tools, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_home_control_refuses_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.delenv("HOME_ASSISTANT_URL", raising=False)
    monkeypatch.delenv("HOME_ASSISTANT_TOKEN", raising=False)
    with pytest.raises(ToolError, match="not connected"):
        await DeviceTools.home_control(DeviceTools(), None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_wifi_status_reports_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")

    async def fake_run(*argv: str, timeout: float = 10.0):
        if argv[:3] == ("nmcli", "-t", "-f"):
            return 0, "HomeWifi\n", ""
        return 0, "", ""

    monkeypatch.setattr("system.devices.run_cmd", fake_run)
    result = await DeviceTools.wifi_control(DeviceTools(), None, action="status")  # type: ignore[arg-type]
    assert "HomeWifi" in result["say"]


@pytest.mark.asyncio
async def test_keyboard_light_no_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")

    async def fake_run(*argv: str, timeout: float = 10.0):
        return 1, "", "not running"

    monkeypatch.setattr("system.devices.run_cmd", fake_run)
    with pytest.raises(ToolError, match="No Razer keyboard"):
        await DeviceTools.keyboard_light(DeviceTools(), None, action="status")  # type: ignore[arg-type]
