"""Desktop-entry launchers cannot forward sandbox tests to the live desktop."""

from pathlib import Path

import pytest

import taskbar


@pytest.mark.parametrize("executable", ["brave-browser", "kcalc"])
def test_sandbox_desktop_entry_launches_directly(tmp_path, monkeypatch, executable):
    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":99")
    monkeypatch.setenv("JARVIS_SANDBOX_BUS_ADDRESS", "unix:path=/isolated/bus")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/live/bus")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    path = tmp_path / "test.desktop"
    path.write_text(f"[Desktop Entry]\nName=Test\nExec={executable} %U\n")
    calls = []
    assert taskbar.launch_desktop(
        path.name,
        dirs=[tmp_path],
        which=lambda name: f"/usr/bin/{name}",
        popen=lambda argv, **kwargs: calls.append((argv, kwargs)),
    )
    argv, options = calls[0]
    assert Path(argv[0]).name == executable
    assert options["env"]["DISPLAY"] == ":99"
    assert options["env"]["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/isolated/bus"
    assert "WAYLAND_DISPLAY" not in options["env"]
    if executable == "brave-browser":
        assert any(
            arg.startswith("--user-data-dir=") and "/sandbox/99/" in arg for arg in argv
        )


@pytest.mark.parametrize(
    "execution",
    ["env APP=1 brave-browser", "flatpak run org.test.App", "custom-launcher"],
)
def test_unsupported_sandbox_entry_never_uses_live_launcher(
    tmp_path, monkeypatch, execution
):
    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":99")
    path = tmp_path / "test.desktop"
    path.write_text(f"[Desktop Entry]\nName=Test\nExec={execution}\n")
    assert not taskbar.launch_desktop(
        path.name,
        dirs=[tmp_path],
        which=lambda name: f"/usr/bin/{name}",
        popen=lambda *args, **kwargs: pytest.fail("unsupported sandbox launch escaped"),
    )
