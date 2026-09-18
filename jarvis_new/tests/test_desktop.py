"""Tests for the desktop-control skill (screenshot -> coordinates -> inject).

Hermetic by design: no test touches the real screen, mouse, or keyboard.
Subprocess and uinput backends are monkeypatched; only pure helpers and
the confirm gate run for real.
"""

import struct

import pytest
from livekit.agents.llm import ToolError

import system.desktop as desktop_module
from system.desktop import (
    KEY_CODES,
    DesktopTools,
    UInputMouse,
    char_to_key,
    clamp1000,
    desktop_size_from_outputs,
    find_label,
    parse_kscreen_outputs,
    parse_tsv_boxes,
    rel_to_abs,
)

SAMPLE_TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num"
    "\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t200\t100\t-1\t\n"
    "2\t1\t1\t0\t0\t0\t10\t10\t180\t80\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t96\tSend\n"
    "5\t1\t1\t1\t1\t2\t55\t10\t50\t20\t92\tmessage\n"
    "5\t1\t1\t1\t1\t3\t110\t10\t10\t20\t-1\t\n"
)

SAMPLE_KSCREEN = (
    "\x1b[01;32mOutput: \x1b[0;0m1 DP-1 some-uuid\n"
    "\x1b[01;32m\tenabled\x1b[0;0m\n"
    "\x1b[01;33m\tGeometry: \x1b[0;0m1920,0 1920x1080\n"
    "Output: 2 HDMI-1 other-uuid\n"
    "\tenabled\n"
    "\tGeometry: 0,0 1920x1080\n"
)


def test_clamp1000() -> None:
    assert clamp1000(-5) == 0
    assert clamp1000(0) == 0
    assert clamp1000(500) == 500
    assert clamp1000(999.6) == 1000
    assert clamp1000(1200) == 1000


def test_rel_to_abs_maps_grid_onto_pixels() -> None:
    assert rel_to_abs(0, 0, 3840, 1080) == (0, 0)
    assert rel_to_abs(1000, 1000, 3840, 1080) == (3839, 1079)
    assert rel_to_abs(500, 500, 2000, 1000) == (999, 499)
    assert rel_to_abs(-10, 2000, 2000, 1000) == (0, 999)


def test_parse_tsv_boxes_keeps_word_rows_only() -> None:
    boxes = parse_tsv_boxes(SAMPLE_TSV)
    assert [(b["text"], b["conf"]) for b in boxes] == [
        ("Send", 96),
        ("message", 92),
    ]
    assert boxes[0]["x0"] == 10
    assert boxes[0]["y1"] == 30


def test_parse_tsv_boxes_rejects_garbage() -> None:
    assert parse_tsv_boxes("") == []
    assert parse_tsv_boxes("not\ttsv\n") == []


def test_find_label_matches_case_insensitively() -> None:
    boxes = parse_tsv_boxes(SAMPLE_TSV)
    hit = find_label(boxes, "send", 200, 100)
    assert hit is not None
    assert hit["text"] == "Send"
    # Box (10,10)-(50,30) in a 200x100 image -> center (30,20) -> (150,200).
    assert (hit["x"], hit["y"]) == (150, 200)


def test_find_label_rejects_miss_and_empty() -> None:
    boxes = parse_tsv_boxes(SAMPLE_TSV)
    assert find_label(boxes, "printer", 200, 100) is None
    assert find_label(boxes, "  ", 200, 100) is None
    assert find_label([], "send", 200, 100) is None


def test_desktop_key_allow_list() -> None:
    for key in (
        "Return",
        "Escape",
        "Tab",
        "Up",
        "Down",
        "Left",
        "Right",
        "BackSpace",
        "Delete",
        "Home",
        "End",
        "Page_Up",
        "Page_Down",
        "F5",
    ):
        assert key in desktop_module.DESKTOP_KEYS
    for key in ("Super_L", "Caps_Lock", "XF86PowerOff", "Enter; rm -rf ~", ""):
        assert key not in desktop_module.DESKTOP_KEYS


def test_key_codes_match_evdev() -> None:
    assert KEY_CODES["Return"] == 28
    assert KEY_CODES["Escape"] == 1
    assert KEY_CODES["Tab"] == 15
    assert KEY_CODES["Up"] == 103
    assert KEY_CODES["F1"] == 59
    assert KEY_CODES["F5"] == 63
    assert KEY_CODES["F12"] == 88


def test_char_to_key_us_layout() -> None:
    assert char_to_key("a") == (30, False)
    assert char_to_key("A") == (30, True)
    assert char_to_key("1") == (2, False)
    assert char_to_key("!") == (2, True)
    assert char_to_key(" ") == (57, False)
    assert char_to_key("-") == (12, False)
    assert char_to_key("_") == (12, True)
    assert char_to_key("€") is None
    assert char_to_key("") is None


def test_parse_kscreen_outputs_strips_ansi() -> None:
    outputs = parse_kscreen_outputs(SAMPLE_KSCREEN)
    assert [(o["num"], o["name"], o["x"], o["y"], o["w"], o["h"]) for o in outputs] == [
        (1, "DP-1", 1920, 0, 1920, 1080),
        (2, "HDMI-1", 0, 0, 1920, 1080),
    ]


def test_parse_kscreen_outputs_rejects_garbage() -> None:
    assert parse_kscreen_outputs("") == []
    assert parse_kscreen_outputs("no outputs here") == []


def test_desktop_size_spans_all_monitors() -> None:
    outputs = parse_kscreen_outputs(SAMPLE_KSCREEN)
    assert desktop_size_from_outputs(outputs) == (3840, 1080)
    assert desktop_size_from_outputs([]) is None


def test_desktop_tool_ids_are_registered() -> None:
    ids = [tool.id for tool in DesktopTools().tools]
    assert ids == [
        "desktop_screenshot",
        "desktop_locate_text",
        "confirm_desktop_action",
        "desktop_click",
        "desktop_type",
        "desktop_key",
        "desktop_scroll",
    ]


def _local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")


def _confirm(tools: DesktopTools) -> None:
    tools._confirmed = "test task"


async def test_click_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local(monkeypatch)
    tools = DesktopTools()
    with pytest.raises(ToolError, match="confirmation"):
        await DesktopTools.desktop_click(tools, None, x=500, y=500)  # type: ignore[arg-type]


async def test_click_moves_and_clicks_then_consumes_confirm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local(monkeypatch)
    tools = DesktopTools()
    _confirm(tools)

    calls: list[tuple] = []

    class FakeMouse:
        def __init__(self, width: int, height: int) -> None:
            calls.append(("open", width, height))

        def move(self, x: int, y: int) -> None:
            calls.append(("move", x, y))

        def click(self, button: str = "left") -> None:
            calls.append(("click", button))

        def close(self) -> None:
            calls.append(("close",))

    monkeypatch.setattr(desktop_module, "UInputMouse", FakeMouse)

    async def fake_size() -> tuple[int, int]:
        return 2000, 1000

    monkeypatch.setattr(desktop_module, "_desktop_size", fake_size)
    result = await DesktopTools.desktop_click(tools, None, x=500, y=250)  # type: ignore[arg-type]
    assert ("move", 999, 249) in calls
    assert ("click", "left") in calls
    assert ("close",) in calls
    assert result["x"] == 500
    # Confirmation is single-use.
    with pytest.raises(ToolError, match="confirmation"):
        await DesktopTools.desktop_click(tools, None, x=500, y=500)  # type: ignore[arg-type]


async def test_click_rejects_bad_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local(monkeypatch)
    tools = DesktopTools()
    _confirm(tools)
    with pytest.raises(ToolError, match="button"):
        await DesktopTools.desktop_click(tools, None, x=100, y=100, button="side")  # type: ignore[arg-type]


async def test_type_requires_confirmation_and_hides_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local(monkeypatch)
    tools = DesktopTools()
    with pytest.raises(ToolError, match="confirmation"):
        await DesktopTools.desktop_type(tools, None, text="hello")  # type: ignore[arg-type]
    _confirm(tools)

    typed: list[str] = []
    wtype_calls: list[tuple[str, ...]] = []

    class FakeMouse:
        def __init__(self, width: int, height: int) -> None:
            pass

        def type_text(self, text: str) -> None:
            typed.append(text)

        def close(self) -> None:
            pass

    monkeypatch.setattr(desktop_module, "UInputMouse", FakeMouse)

    async def fake_size() -> tuple[int, int]:
        return 2000, 1000

    monkeypatch.setattr(desktop_module, "_desktop_size", fake_size)

    async def fake_run(*argv: str, timeout: float = 10.0):
        wtype_calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr(desktop_module, "run_cmd", fake_run)
    secret = "s3cret-password!"
    result = await DesktopTools.desktop_type(tools, None, text=secret)  # type: ignore[arg-type]
    assert typed == [secret]
    assert wtype_calls == []  # uinput primary, no fallback
    assert secret not in result["say"]
    assert secret not in str(result)


async def test_type_falls_back_to_wtype_without_uinput(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _local(monkeypatch)
    tools = DesktopTools()
    _confirm(tools)

    class NoMouse:
        def __init__(self, width: int, height: int) -> None:
            raise desktop_module.DesktopError("no uinput")

    monkeypatch.setattr(desktop_module, "UInputMouse", NoMouse)

    async def fake_size() -> tuple[int, int]:
        return 2000, 1000

    monkeypatch.setattr(desktop_module, "_desktop_size", fake_size)

    calls: list[tuple[str, ...]] = []

    async def fake_run(*argv: str, timeout: float = 10.0):
        calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr(desktop_module, "run_cmd", fake_run)
    result = await DesktopTools.desktop_type(tools, None, text="hi")  # type: ignore[arg-type]
    assert calls and calls[0][:2] == ("wtype", "--")
    assert result["typed"] == 2


async def test_type_validates_length(monkeypatch: pytest.MonkeyPatch) -> None:
    _local(monkeypatch)
    tools = DesktopTools()
    _confirm(tools)
    with pytest.raises(ToolError):
        await DesktopTools.desktop_type(tools, None, text="   ")  # type: ignore[arg-type]
    with pytest.raises(ToolError):
        await DesktopTools.desktop_type(tools, None, text="x" * 501)  # type: ignore[arg-type]


async def test_key_validates_and_presses(monkeypatch: pytest.MonkeyPatch) -> None:
    _local(monkeypatch)
    tools = DesktopTools()
    with pytest.raises(ToolError, match="confirmation"):
        await DesktopTools.desktop_key(tools, None, key="Return")  # type: ignore[arg-type]
    _confirm(tools)
    with pytest.raises(ToolError, match="key"):
        await DesktopTools.desktop_key(tools, None, key="Super_L")  # type: ignore[arg-type]

    taps: list[int] = []

    class FakeMouse:
        def __init__(self, width: int, height: int) -> None:
            pass

        def tap(self, code: int) -> None:
            taps.append(code)

        def close(self) -> None:
            pass

    monkeypatch.setattr(desktop_module, "UInputMouse", FakeMouse)

    async def fake_size() -> tuple[int, int]:
        return 2000, 1000

    monkeypatch.setattr(desktop_module, "_desktop_size", fake_size)
    result = await DesktopTools.desktop_key(tools, None, key="Return")  # type: ignore[arg-type]
    assert taps == [28]
    assert result["key"] == "Return"


async def test_scroll_validates_and_wheels(monkeypatch: pytest.MonkeyPatch) -> None:
    _local(monkeypatch)
    tools = DesktopTools()
    with pytest.raises(ToolError, match="confirmation"):
        await DesktopTools.desktop_scroll(tools, None, direction="down")  # type: ignore[arg-type]
    _confirm(tools)
    with pytest.raises(ToolError, match="direction"):
        await DesktopTools.desktop_scroll(tools, None, direction="sideways")  # type: ignore[arg-type]

    calls: list[tuple] = []

    class FakeMouse:
        def __init__(self, width: int, height: int) -> None:
            pass

        def scroll(self, dx: int, dy: int) -> None:
            calls.append((dx, dy))

        def close(self) -> None:
            pass

    monkeypatch.setattr(desktop_module, "UInputMouse", FakeMouse)

    async def fake_size() -> tuple[int, int]:
        return 2000, 1000

    monkeypatch.setattr(desktop_module, "_desktop_size", fake_size)
    result = await DesktopTools.desktop_scroll(tools, None, direction="down", times=2)  # type: ignore[arg-type]
    assert calls == [(0, -1), (0, -1)]
    assert result["direction"] == "down"


async def test_screenshot_returns_path_and_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _local(monkeypatch)
    from PIL import Image

    png = tmp_path / "full-1.png"
    Image.new("RGB", (200, 100)).save(png)

    async def fake_shot(self, context, monitor=None):
        return {"path": str(png), "say": "saved"}

    from system.core import SystemTools

    monkeypatch.setattr(SystemTools, "take_os_screenshot", fake_shot)
    tools = DesktopTools()
    result = await DesktopTools.desktop_screenshot(tools, None)  # type: ignore[arg-type]
    assert result["path"] == str(png)
    assert (result["width"], result["height"]) == (200, 100)


async def test_locate_text_returns_grid_coords(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _local(monkeypatch)
    from PIL import Image

    png = tmp_path / "full-1.png"
    Image.new("RGB", (200, 100)).save(png)

    async def fake_shot(self, context, monitor=None):
        return {"path": str(png), "say": "saved"}

    from system.core import SystemTools

    monkeypatch.setattr(SystemTools, "take_os_screenshot", fake_shot)

    async def fake_run(*argv: str, timeout: float = 10.0):
        assert argv[0] == "tesseract"
        return 0, SAMPLE_TSV, ""

    monkeypatch.setattr(desktop_module, "run_cmd", fake_run)
    tools = DesktopTools()
    result = await DesktopTools.desktop_locate_text(tools, None, label="send")  # type: ignore[arg-type]
    assert (result["x"], result["y"]) == (150, 200)
    assert result["matched"] == "Send"


async def test_locate_text_reports_miss_and_ocr_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _local(monkeypatch)
    from PIL import Image

    png = tmp_path / "full-1.png"
    Image.new("RGB", (200, 100)).save(png)

    async def fake_shot(self, context, monitor=None):
        return {"path": str(png), "say": "saved"}

    from system.core import SystemTools

    monkeypatch.setattr(SystemTools, "take_os_screenshot", fake_shot)
    tools = DesktopTools()

    async def fake_empty(*argv: str, timeout: float = 10.0):
        return 0, SAMPLE_TSV, ""

    monkeypatch.setattr(desktop_module, "run_cmd", fake_empty)
    with pytest.raises(ToolError, match="can't see"):
        await DesktopTools.desktop_locate_text(tools, None, label="printer")  # type: ignore[arg-type]

    async def fake_fail(*argv: str, timeout: float = 10.0):
        return 1, "", "boom"

    monkeypatch.setattr(desktop_module, "run_cmd", fake_fail)
    with pytest.raises(ToolError, match="could not read"):
        await DesktopTools.desktop_locate_text(tools, None, label="send")  # type: ignore[arg-type]


def _mock_libc(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes as _ctypes
    from unittest.mock import MagicMock

    fake = MagicMock()
    fake.ioctl.return_value = 0
    monkeypatch.setattr(_ctypes, "CDLL", lambda *a, **k: fake)


def test_uinput_mouse_emits_move_and_click_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    written: list[bytes] = []
    _mock_libc(monkeypatch)
    monkeypatch.setattr("os.open", lambda path, flags: 7)
    monkeypatch.setattr(
        "os.write", lambda fd, data: written.append(bytes(data)) or len(data)
    )
    monkeypatch.setattr("os.close", lambda fd: None)
    monkeypatch.setattr("time.sleep", lambda s: None)

    mouse = UInputMouse(2000, 1000)
    written.clear()
    mouse.move(1000, 500)
    mouse.click("left")
    mouse.close()

    # input_event is 24 bytes: (tv_sec, tv_usec, type, code, value).
    events = [struct.unpack("llHHi", blob) for blob in written]
    kinds = [(etype, ecode, evalue) for (_, _, etype, ecode, evalue) in events]
    assert (3, 0, 1000) in kinds  # ABS_X
    assert (3, 1, 500) in kinds  # ABS_Y
    assert (1, 272, 1) in kinds  # BTN_LEFT press
    assert (1, 272, 0) in kinds  # BTN_LEFT release
    assert (0, 0, 0) in kinds  # SYN_REPORT

    written.clear()
    mouse.tap(30)  # KEY_A
    mouse.type_text("Hi!")
    mouse.close()
    events = [struct.unpack("llHHi", blob) for blob in written]
    kinds = [(etype, ecode, evalue) for (_, _, etype, ecode, evalue) in events]
    assert (1, 30, 1) in kinds  # KEY_A press
    assert (1, 30, 0) in kinds  # KEY_A release
    assert (1, 42, 1) in kinds  # LEFTSHIFT for capitals
    assert (1, 42, 0) in kinds  # LEFTSHIFT release
    with pytest.raises(desktop_module.DesktopError, match="not typable"):
        mouse.type_text("€")


def test_uinput_mouse_rejects_bad_button_and_missing_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_libc(monkeypatch)
    monkeypatch.setattr("os.open", lambda path, flags: 7)
    monkeypatch.setattr("os.write", lambda fd, data: len(data))
    monkeypatch.setattr("os.close", lambda fd: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    mouse = UInputMouse(1920, 1080)
    with pytest.raises(desktop_module.DesktopError, match="button"):
        mouse.click("side")
    mouse.close()

    monkeypatch.setattr(desktop_module, "_UINPUT_PATH", "/nonexistent/uinput")

    def _noent(path, flags):
        raise FileNotFoundError(2, "No such file or directory", path)

    monkeypatch.setattr("os.open", _noent)
    with pytest.raises(desktop_module.DesktopError, match="uinput"):
        UInputMouse(1920, 1080)
