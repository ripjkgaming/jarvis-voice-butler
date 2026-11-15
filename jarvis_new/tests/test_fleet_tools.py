"""Fleet device tools: per-platform argv, validation, degradation. No real OS calls."""

import base64
import json
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fleet_tools

PLAT = {"windows": "win32", "linux": "linux", "macos": "darwin"}
PNG = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\rIHDR"
    + struct.pack(">II", 1920, 1080)
    + b"\x00" * 16
)
CAPTURE_BINS = ("grim", "spectacle", "scrot", "screencapture")


class FakeRun:
    """Stands in for subprocess.run. Records every call; never runs anything."""

    def __init__(self):
        self.calls: list[tuple[list, dict]] = []
        self.missing: set[str] = set()
        self.stdout = ""
        self.stderr = ""
        self.returncode = 0
        self.png: bytes | None = PNG

    def __call__(self, argv, **kwargs):
        assert isinstance(argv, list), "argv must be a list"
        assert not kwargs.get("shell", False), "shell=True is forbidden"
        assert kwargs.get("timeout"), "every subprocess needs a timeout"
        self.calls.append((list(argv), kwargs))
        if argv[0] in self.missing:
            raise FileNotFoundError(argv[0])
        env = kwargs.get("env") or {}
        target = env.get("JF_OUT") or (argv[-1] if argv[0] in CAPTURE_BINS else None)
        if target and self.png is not None:
            Path(target).write_bytes(self.png)
        return subprocess.CompletedProcess(
            argv, self.returncode, self.stdout, self.stderr
        )

    def argv(self, index=-1):
        return self.calls[index][0]

    def env(self, index=-1):
        return self.calls[index][1].get("env") or {}


@pytest.fixture
def fake(monkeypatch):
    rec = FakeRun()
    monkeypatch.setattr(fleet_tools.subprocess, "run", rec)
    return rec


def as_platform(monkeypatch, os_name):
    monkeypatch.setattr(fleet_tools, "_platform", lambda: PLAT[os_name])


# ------------------------------------------------------------ run() contract
def test_unknown_tool_is_error_and_runs_nothing(fake):
    res = fleet_tools.run("format_disk", None)
    assert res["ok"] is False and "unknown tool" in res["error"]
    assert fake.calls == []


def test_args_must_be_an_object(fake):
    assert fleet_tools.run("lock", ["x"])["ok"] is False
    assert fake.calls == []


def test_run_never_raises(fake, monkeypatch):
    def boom(args):
        raise RuntimeError("secret detail")

    monkeypatch.setitem(fleet_tools._HANDLERS, "lock", boom)
    res = fleet_tools.run("lock", {})
    assert res == {"ok": False, "error": "lock failed: RuntimeError"}


def test_bad_type_is_reported_not_raised(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    assert fleet_tools.run("notify", {"title": 5, "message": "x"}) == {
        "ok": False,
        "error": "title must be text",
    }
    assert fake.calls == []


def test_tool_table_is_safe_and_complete():
    assert set(fleet_tools.TOOLS) == {
        "sysinfo",
        "lock",
        "notify",
        "open_url",
        "open_app",
        "volume",
        "media",
        "screenshot",
    }
    for spec in fleet_tools.TOOLS.values():
        assert {"description", "args", "platforms"} <= spec.keys()
        assert set(spec["platforms"]) == {"windows", "linux", "macos"}


def test_source_has_no_shell_or_posix_only_modules():
    text = Path(fleet_tools.__file__).read_text()
    for banned in ("shell=True", "os.system", "fcntl", "os.popen"):
        assert banned not in text


def test_platform_helper_reads_sys_platform(fake, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    fleet_tools.run("lock", {})
    assert fake.argv() == ["pmset", "displaysleepnow"]


# --------------------------------------------------------------------- lock
@pytest.mark.parametrize(
    "os_name, argv",
    [
        ("windows", ["rundll32.exe", "user32.dll,LockWorkStation"]),
        ("linux", ["loginctl", "lock-session"]),
        ("macos", ["pmset", "displaysleepnow"]),
    ],
)
def test_lock_argv_per_platform(fake, monkeypatch, os_name, argv):
    as_platform(monkeypatch, os_name)
    assert fleet_tools.run("lock", {})["ok"] is True
    assert fake.argv() == argv


# ------------------------------------------------------------------- notify
def test_notify_windows_passes_values_via_env_only(fake, monkeypatch):
    as_platform(monkeypatch, "windows")
    title = 'x" & (do shell script "calc") & "'
    message = "$(calc) `rm`; 'quote'"
    assert fleet_tools.run("notify", {"title": title, "message": message})["ok"] is True
    assert fake.env()["JF_TITLE"] == title and fake.env()["JF_MESSAGE"] == message
    assert not any(title in part or message in part for part in fake.argv())


def test_notify_linux_argv(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    fleet_tools.run("notify", {"title": "Hi", "message": "Dinner at 7"})
    assert fake.argv() == ["notify-send", "--", "Hi", "Dinner at 7"]


def test_notify_macos_uses_argv_not_interpolation(fake, monkeypatch):
    as_platform(monkeypatch, "macos")
    title, message = 'a" & quit & "', 'b\\" & beep'
    fleet_tools.run("notify", {"title": title, "message": message})
    argv = fake.argv()
    assert argv[-2:] == [title, message]
    scripts = [argv[i + 1] for i, part in enumerate(argv) if part == "-e"]
    assert not any(title in s or message in s for s in scripts)


def test_notify_validation(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    assert fleet_tools.run("notify", {"title": "t"}) == {
        "ok": False,
        "error": "message is required",
    }
    assert (
        fleet_tools.run("notify", {"message": "m", "title": "t" * 101})["ok"] is False
    )
    assert fake.calls == []


# ---------------------------------------------------------------- open_url
@pytest.mark.parametrize(
    "bad",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://",
        "ftp://files.example.com",
        "https://a b.example.com",
        'https://x.example.com/"&calc',
        "http://exa\nmple.com",
        42,
    ],
)
def test_open_url_rejects_bad_urls(fake, monkeypatch, bad):
    as_platform(monkeypatch, "linux")
    assert fleet_tools.run("open_url", {"url": bad})["ok"] is False
    assert fake.calls == []


@pytest.mark.parametrize(
    "os_name, argv",
    [
        (
            "windows",
            [
                "rundll32.exe",
                "url.dll,FileProtocolHandler",
                "https://example.com/a?b=1&c=2",
            ],
        ),
        ("linux", ["xdg-open", "https://example.com/a?b=1&c=2"]),
        ("macos", ["open", "https://example.com/a?b=1&c=2"]),
    ],
)
def test_open_url_argv_per_platform(fake, monkeypatch, os_name, argv):
    as_platform(monkeypatch, os_name)
    assert (
        fleet_tools.run("open_url", {"url": "https://example.com/a?b=1&c=2"})["ok"]
        is True
    )
    assert fake.argv() == argv


# ---------------------------------------------------------------- open_app
@pytest.mark.parametrize(
    "bad", ["a;rm", "-x", "", "x" * 65, "app&calc", "name|x", 'a"b', "..\\x", "a\nb"]
)
def test_open_app_rejects_bad_names(fake, monkeypatch, bad):
    as_platform(monkeypatch, "windows")
    assert fleet_tools.run("open_app", {"name": bad})["ok"] is False
    assert fake.calls == []


@pytest.mark.parametrize(
    "os_name, argv",
    [
        ("windows", ["cmd", "/c", "start", "", "Visual Studio Code"]),
        ("linux", ["gtk-launch", "Visual Studio Code"]),
        ("macos", ["open", "-a", "Visual Studio Code"]),
    ],
)
def test_open_app_argv_per_platform(fake, monkeypatch, os_name, argv):
    as_platform(monkeypatch, os_name)
    assert fleet_tools.run("open_app", {"name": "Visual Studio Code"})["ok"] is True
    assert fake.argv() == argv


# ------------------------------------------------------------------ volume
@pytest.mark.parametrize(
    "args",
    [
        {"action": "mute_all"},
        {"action": "set", "level": 101},
        {"action": "set", "level": -1},
        {"action": "set", "level": True},
        {"action": "set", "level": "loud"},
        {"action": "set", "level": 50.5},
        {"action": "set"},
    ],
)
def test_volume_validation(fake, monkeypatch, args):
    as_platform(monkeypatch, "linux")
    assert fleet_tools.run("volume", args)["ok"] is False
    assert fake.calls == []


def test_volume_linux_set_uses_wpctl(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    assert fleet_tools.run("volume", {"action": "set", "level": 42})["ok"] is True
    assert fake.argv() == ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "42%"]


def test_volume_linux_falls_back_to_pactl(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    fake.missing = {"wpctl"}
    assert fleet_tools.run("volume", {"action": "up"})["ok"] is True
    assert fake.argv() == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"]


def test_volume_linux_unavailable_when_no_audio_tool(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    fake.missing = {"wpctl", "pactl"}
    assert fleet_tools.run("volume", {"action": "mute"}) == {
        "ok": False,
        "error": "volume unavailable on this system",
    }


def test_volume_linux_get_parses_wpctl_output(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    fake.stdout = "Volume: 0.45 [MUTED]\n"
    res = fleet_tools.run("volume", {"action": "get"})
    assert res["ok"] is True and res["level"] == 45 and res["muted"] is True


def test_volume_macos_set_passes_level_as_argv(fake, monkeypatch):
    as_platform(monkeypatch, "macos")
    assert fleet_tools.run("volume", {"action": "set", "level": 42})["ok"] is True
    argv = fake.argv()
    assert argv[-1] == "42"
    assert not any("42" in part for part in argv[:-1])


def test_volume_macos_mute(fake, monkeypatch):
    as_platform(monkeypatch, "macos")
    fleet_tools.run("volume", {"action": "mute"})
    assert (
        fake.argv()[:2] == ["osascript", "-e"]
        and "set volume with output muted" in fake.argv()
    )


def test_volume_windows_keystrokes_via_env(fake, monkeypatch):
    as_platform(monkeypatch, "windows")
    fleet_tools.run("volume", {"action": "up"})
    assert fake.env()["JF_KEY"] == "175" and fake.env()["JF_COUNT"] == "5"
    assert fake.argv()[:4] == [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
    ]


def test_volume_windows_set_drops_then_climbs(fake, monkeypatch):
    as_platform(monkeypatch, "windows")
    assert fleet_tools.run("volume", {"action": "set", "level": 42})["ok"] is True
    assert len(fake.calls) == 2
    first, second = (env for _, kw in fake.calls for env in [kw["env"]])
    assert (first["JF_KEY"], first["JF_COUNT"]) == ("174", "50")
    assert (second["JF_KEY"], second["JF_COUNT"]) == ("175", "21")


def test_volume_windows_get_is_explained_error(fake, monkeypatch):
    as_platform(monkeypatch, "windows")
    res = fleet_tools.run("volume", {"action": "get"})
    assert res["ok"] is False and "not available on Windows" in res["error"]


# ------------------------------------------------------------------- media
@pytest.mark.parametrize(
    "os_name, argv",
    [
        ("linux", ["playerctl", "next"]),
        ("linux", ["playerctl", "previous"]),
    ],
)
def test_media_linux(fake, monkeypatch, os_name, argv):
    as_platform(monkeypatch, os_name)
    action = "next" if argv[1] == "next" else "prev"
    assert fleet_tools.run("media", {"action": action})["ok"] is True
    assert fake.argv() == argv


def test_media_windows_media_key(fake, monkeypatch):
    as_platform(monkeypatch, "windows")
    fleet_tools.run("media", {"action": "play_pause"})
    assert fake.env()["JF_KEY"] == "179" and fake.env()["JF_COUNT"] == "1"


def test_media_macos_action_is_argv_not_script(fake, monkeypatch):
    as_platform(monkeypatch, "macos")
    fleet_tools.run("media", {"action": "next"})
    assert fake.argv()[-1] == "next track"
    assert "next track" not in " ".join(fake.argv()[:-1])


def test_media_rejects_unknown_action(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    assert fleet_tools.run("media", {"action": "stop"})["ok"] is False
    assert fake.calls == []


# --------------------------------------------------------- missing binaries
@pytest.mark.parametrize(
    "tool, args, os_name, missing, expected",
    [
        ("lock", None, "linux", {"loginctl"}, "lock unavailable on this system"),
        (
            "media",
            {"action": "next"},
            "linux",
            {"playerctl"},
            "media unavailable on this system",
        ),
        (
            "notify",
            {"message": "hi"},
            "linux",
            {"notify-send"},
            "notify unavailable on this system",
        ),
        (
            "open_url",
            {"url": "https://example.com"},
            "linux",
            {"xdg-open"},
            "open_url unavailable on this system",
        ),
        (
            "open_app",
            {"name": "Spotify"},
            "macos",
            {"open"},
            "open_app unavailable on this system",
        ),
        (
            "volume",
            {"action": "up"},
            "windows",
            {"powershell"},
            "volume unavailable on this system",
        ),
        (
            "screenshot",
            None,
            "linux",
            {"grim", "spectacle", "scrot"},
            "screenshot unavailable on this system",
        ),
        (
            "screenshot",
            None,
            "macos",
            {"screencapture"},
            "screenshot unavailable on this system",
        ),
    ],
)
def test_missing_binary_degrades(
    fake, monkeypatch, tool, args, os_name, missing, expected
):
    as_platform(monkeypatch, os_name)
    fake.missing = missing
    assert fleet_tools.run(tool, args) == {"ok": False, "error": expected}


def test_nonzero_exit_reports_failure(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    fake.returncode, fake.stderr = 1, "Failed to connect to bus\n"
    res = fleet_tools.run("lock", None)
    assert res == {
        "ok": False,
        "error": "lock failed (exit 1): Failed to connect to bus",
    }


# ----------------------------------------------------------------- sysinfo
def test_sysinfo_survives_missing_powershell(fake, monkeypatch):
    as_platform(monkeypatch, "windows")
    fake.missing = {"powershell"}
    res = fleet_tools.run("sysinfo", None)
    assert res["ok"] is True
    assert (
        res["platform"] == "windows"
        and res["memory"] is None
        and res["battery_pct"] is None
    )
    assert "hostname" in res and "python" in res and "disk" in res


def test_sysinfo_windows_parses_probe_json(fake, monkeypatch):
    as_platform(monkeypatch, "windows")
    fake.stdout = json.dumps(
        {"total_kb": 1000, "free_kb": 500, "uptime_s": 42, "battery": 80}
    )
    res = fleet_tools.run("sysinfo", None)
    assert res["memory"] == {"total_bytes": 1024000, "available_bytes": 512000}
    assert res["uptime_s"] == 42 and res["battery_pct"] == 80


def test_sysinfo_on_real_linux_reports_core_fields(monkeypatch):
    as_platform(monkeypatch, "linux")
    res = fleet_tools.run("sysinfo", {})
    assert (
        res["ok"] is True
        and res["platform"] == "linux"
        and res["cpu_count"] is not None
    )


# -------------------------------------------------------------- screenshot
def test_screenshot_linux_returns_png_and_removes_temp(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    res = fleet_tools.run("screenshot", None)
    assert res["ok"] is True and (res["width"], res["height"]) == (1920, 1080)
    assert base64.b64decode(res["png_b64"]) == PNG
    assert not Path(fake.argv()[-1]).exists()


def test_screenshot_falls_through_to_spectacle(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    fake.missing = {"grim"}
    assert fleet_tools.run("screenshot", None)["ok"] is True
    assert fake.calls[1][0][0] == "spectacle" and "-b" in fake.calls[1][0]


def test_screenshot_windows_uses_env_path(fake, monkeypatch):
    as_platform(monkeypatch, "windows")
    res = fleet_tools.run("screenshot", None)
    assert res["ok"] is True
    assert not Path(fake.env()["JF_OUT"]).exists()


def test_screenshot_macos_argv(fake, monkeypatch):
    as_platform(monkeypatch, "macos")
    assert fleet_tools.run("screenshot", None)["ok"] is True
    assert fake.argv()[:2] == ["screencapture", "-x"]


def test_screenshot_caps_size_and_cleans_up(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    fake.png = PNG + b"\x00" * (6 * 1024 * 1024)
    res = fleet_tools.run("screenshot", None)
    assert res == {"ok": False, "error": "screenshot is larger than 6 MB"}
    assert not Path(fake.argv()[-1]).exists()


def test_screenshot_rejects_non_png_output(fake, monkeypatch):
    as_platform(monkeypatch, "linux")
    fake.png = b"not an image"
    assert fleet_tools.run("screenshot", None) == {
        "ok": False,
        "error": "screenshot produced no PNG",
    }


# ------------------------------------------------------------- partner accounts
def test_partner_accounts_from_config_and_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "fleet").mkdir()
    (tmp_path / "fleet" / "config.json").write_text(
        json.dumps({"partner_accounts": ["Partner@Example.com", "not-an-email", 7]})
    )
    monkeypatch.setenv(
        "JARVIS_FLEET_PARTNERS", " partner@example.com , second@example.org,,bad"
    )
    assert fleet_tools.partner_accounts() == [
        "partner@example.com",
        "second@example.org",
    ]


def test_partner_accounts_missing_or_corrupt_config(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.delenv("JARVIS_FLEET_PARTNERS", raising=False)
    assert fleet_tools.partner_accounts() == []
    (tmp_path / "fleet").mkdir()
    (tmp_path / "fleet" / "config.json").write_text("{not json")
    assert fleet_tools.partner_accounts() == []
