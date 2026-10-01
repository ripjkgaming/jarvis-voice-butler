"""Surface regression tests: every screenshot and input device is a fake."""

import asyncio
import signal
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import system.computer_surface as surface_module
import system.desktop as desktop
from system.computer_surface import ComputerSurface, SceneChangedError, SurfaceError

REGION = {"x": 0, "y": 0, "width": 600, "height": 500}


def framed(action):
    action = dict(action)
    action["region"] = dict(REGION)
    if action.get("x") == 1000:
        action["region"] = {"x": 500, "y": 500, "width": 500, "height": 500}
    if action.get("type") == "scroll":
        action.setdefault("x", 20)
        action.setdefault("y", 20)
    return action


@pytest.fixture
def fake_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.delenv("JARVIS_DESKTOP_SANDBOX", raising=False)
    png = tmp_path / "source.png"
    Image.new("RGB", (200, 100), "white").save(png)
    calls = []
    state = {"path": str(png), "width": 200, "height": 100}

    async def capture_command(self, argv):
        calls.append(("screenshot",))
        target = Path(argv[-1])
        assert target.parent == self._workspace
        assert target.name.startswith("computer-capture-")
        assert target.stat().st_mode & 0o777 == 0o600
        if state.get("symlink"):
            target.unlink()
            target.symlink_to(png)
        else:
            target.write_bytes(Path(state["path"]).read_bytes())
        return 0

    async def forbidden_legacy_capture(*args, **kwargs):
        raise AssertionError("shared desktop screenshot path must never be used")

    async def focus(self):
        signature = (
            "desktop",
            state.get("window_id", "window-1"),
            "editor",
            42,
            state.get("frame", (0, 0, state["width"], state["height"])),
            state.get("focus_id", "window-1"),
        )
        return SimpleNamespace(
            signature=signature,
            window_id=signature[1],
            app="editor",
            pid=42,
            focus_id=signature[-1],
            frame=signature[4],
            desktop=(0, 0, state["width"], state["height"]),
            overlays=state.get("overlays", ()),
            observed_at=__import__("time").monotonic(),
        )

    async def size():
        return state["width"], state["height"]

    class Input:
        def __init__(self, *args):
            calls.append(("open", *args))

        def move(self, x, y):
            calls.append(("move", x, y))

        def click(self, button):
            calls.append(("click", button))

        def type_text(self, text):
            calls.append(("type", text))

        def tap(self, code):
            calls.append(("press", code))

        def scroll(self, dx, dy):
            calls.append(("scroll", dx, dy))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(
        desktop.DesktopTools, "desktop_screenshot", forbidden_legacy_capture
    )
    monkeypatch.setattr(
        ComputerSurface, "_run_capture_command", capture_command, raising=False
    )
    monkeypatch.setattr(desktop, "_desktop_size", size)
    monkeypatch.setattr(ComputerSurface, "_focus_snapshot", focus, raising=False)
    monkeypatch.setattr(desktop, "UInputMouse", Input)
    monkeypatch.setattr(desktop, "XdoInput", Input)
    return calls, state, png, Input


async def test_observation_has_verified_private_evidence(fake_desktop, tmp_path):
    calls, _, source, _ = fake_desktop
    workspace = tmp_path / "observations"
    surface = ComputerSurface(workspace)
    observation = await surface.observe()
    assert (observation.width, observation.height) == (200, 100)
    assert observation.id and len(observation.sha256) == 64
    assert len(observation.pixels_sha256) == 64
    assert observation.target == "desktop"
    assert observation.created_at > 0
    path = Path(observation.path)
    assert path.parent == workspace
    assert path.read_bytes() == source.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    assert calls == [("screenshot",)]
    assert not list(workspace.glob("computer-capture-*"))
    unrelated = workspace / "keep.txt"
    unrelated.write_text("keep")
    await surface.close()
    assert not path.exists()
    assert unrelated.read_text() == "keep"
    assert source.exists()


async def test_actions_ground_then_observe_and_reuse_device(fake_desktop, tmp_path):
    calls, _, _, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    actions = [
        {"type": "click", "x": 500, "y": 250},
        {"type": "move", "x": 1000, "y": 1000},
        {"type": "click", "x": 500, "y": 250},
        {"type": "type", "text": "Secret pass phrase!"},
        {"type": "click", "x": 500, "y": 250},
        {"type": "press", "key": "Return"},
        {"type": "scroll", "direction": "down", "times": 2},
    ]
    for action in actions:
        previous = observation
        observation = await surface.act(framed(action), previous.id)
        assert observation.id != previous.id
    assert calls.count(("open", 200, 100)) == 1
    assert ("move", 99, 24) in calls
    assert ("move", 199, 99) in calls
    assert ("click", "left") in calls
    assert ("press", desktop.KEY_CODES["Return"]) in calls
    assert calls.count(("scroll", 0, -1)) == 2
    assert calls[-1] == ("screenshot",)
    assert "Secret pass phrase!" not in repr(observation)
    await surface.close()
    assert calls.count(("close",)) == 1


@pytest.mark.parametrize(
    "action",
    [
        {"type": "click", "x": -1, "y": 2},
        {"type": "click", "x": 1, "y": 1001},
        {"type": "move", "x": True, "y": 2},
        {"type": "move", "x": 1.5, "y": 2},
        {"type": "click", "x": 1, "y": 2, "button": "side"},
        {"type": "click", "x": 1, "y": 2, "url": "https://example.com"},
        {"type": "press", "key": "Super_L"},
        {"type": "press", "key": "Ctrl+L"},
        {"type": "press", "key": ["Return"]},
        {"type": "type", "text": "submit\nnow"},
        {"type": "type", "text": "tab\taway"},
        {"type": "type", "text": "line\rreturn"},
        {"type": "type", "text": "\x1b"},
        {"type": "type", "text": "\x7f"},
        {"type": "type", "text": "café"},
        {"type": "type", "text": "x" * 501},
        {"type": "type", "text": ""},
        {"type": "scroll", "direction": "sideways"},
        {"type": "scroll", "direction": "down", "times": 6},
        {"type": "scroll", "direction": "down", "times": True},
        {"type": "shell", "command": "whoami"},
        {"type": "move", "x": 1},
    ],
)
async def test_invalid_action_never_arms_or_injects(action, fake_desktop, tmp_path):
    calls, _, _, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    before = list(calls)
    with pytest.raises(SurfaceError):
        await surface.act(framed(action), observation.id)
    assert calls == before
    await surface.close()


async def test_missing_stale_and_consumed_evidence_refused(fake_desktop, tmp_path):
    calls, _, _, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence", max_observation_age_s=0.01)
    action = {"type": "click", "x": 1, "y": 1}
    with pytest.raises(SurfaceError, match="observation"):
        await surface.act(framed(action), "made-up")
    observation = await surface.observe()
    await asyncio.sleep(0.02)
    with pytest.raises(SurfaceError, match="stale"):
        await surface.act(framed(action), observation.id)
    assert not any(call[0] == "open" for call in calls)
    await surface.close()
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    await surface.act(framed(action), observation.id)
    with pytest.raises(SurfaceError, match="observation"):
        await surface.act(framed(action), observation.id)
    await surface.close()


async def test_changed_scene_blocks_click(fake_desktop, tmp_path):
    calls, _, source, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    Image.new("RGB", (200, 100), "black").save(source)
    with pytest.raises(SceneChangedError, match="changed"):
        await surface.act(framed({"type": "click", "x": 20, "y": 20}), observation.id)
    assert not any(call[0] == "open" for call in calls)
    await surface.close()


async def test_changed_geometry_closes_device(fake_desktop, tmp_path):
    calls, state, source, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    first = await surface.observe()
    second = await surface.act(framed({"type": "move", "x": 10, "y": 10}), first.id)
    state["width"] = 300
    Image.new("RGB", (300, 100), "white").save(source)
    with pytest.raises(SurfaceError, match=r"geometry|changed"):
        await surface.act(framed({"type": "click", "x": 20, "y": 20}), second.id)
    assert calls.count(("close",)) == 1
    assert not any(call[0] == "click" for call in calls)
    await surface.close()


@pytest.mark.parametrize("bad_kind", ["unreadable", "size", "missing", "symlink"])
async def test_bad_screenshot_cannot_become_evidence(bad_kind, fake_desktop, tmp_path):
    _, state, source, _ = fake_desktop
    if bad_kind == "unreadable":
        source.write_bytes(b"not an image")
    elif bad_kind == "size":
        state["width"] = 123
    elif bad_kind == "missing":
        source.unlink()
    else:
        state["symlink"] = True
    surface = ComputerSurface(tmp_path / "evidence")
    with pytest.raises(SurfaceError):
        await surface.observe()
    assert not list((tmp_path / "evidence").glob("computer-capture-*"))
    await surface.close()


def test_invalid_sandbox_cannot_fall_through_to_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", "not-a-display")
    with pytest.raises(SurfaceError, match="sandbox"):
        ComputerSurface(tmp_path)


async def test_sandbox_is_pinned_and_never_uses_real_input(
    fake_desktop, monkeypatch, tmp_path
):
    calls, _, _, _ = fake_desktop
    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":91")

    def forbidden(*args):
        raise AssertionError("real uinput must never be opened")

    monkeypatch.setattr(desktop, "UInputMouse", forbidden)
    surface = ComputerSurface(tmp_path / "evidence")
    assert surface.capabilities["isolated"] is True
    observation = await surface.observe()
    observation = await surface.act(
        framed({"type": "click", "x": 20, "y": 20}), observation.id
    )
    await surface.act(framed({"type": "type", "text": "hello"}), observation.id)
    assert ("open", ":91") in calls
    await surface.close()


async def test_sandbox_failure_never_falls_back(fake_desktop, monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":91")
    calls, _, _, _ = fake_desktop

    def fail(*args):
        raise desktop.DesktopError("no xdotool")

    async def forbidden(*args, **kwargs):
        raise AssertionError("fallback subprocess must never run")

    monkeypatch.setattr(desktop, "XdoInput", fail)
    monkeypatch.setattr(desktop, "run_cmd", forbidden)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    with pytest.raises(SurfaceError, match="input"):
        await surface.act(framed({"type": "click", "x": 20, "y": 20}), observation.id)
    assert not any(call[0] == "open" for call in calls)
    await surface.close()


async def test_target_drift_and_nonlocal_refuse_before_capture(
    fake_desktop, monkeypatch, tmp_path
):
    calls, _, _, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":92")
    with pytest.raises(SurfaceError, match="target"):
        await surface.observe()
    assert calls == []
    monkeypatch.delenv("JARVIS_DESKTOP_SANDBOX")
    monkeypatch.setenv("JARVIS_LOCAL", "0")
    with pytest.raises(SurfaceError, match=r"local|own machine"):
        await surface.observe()
    assert calls == []
    await surface.close()


async def test_cancel_drains_injection_before_releasing_lock(
    fake_desktop, monkeypatch, tmp_path
):
    calls, _, _, input_class = fake_desktop
    started, release = threading.Event(), threading.Event()

    class SlowInput(input_class):
        def type_text(self, text):
            started.set()
            assert release.wait(5)
            calls.append(("typed",))

    monkeypatch.setattr(desktop, "UInputMouse", SlowInput)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(
        framed({"type": "click", "x": 20, "y": 20}), observation.id
    )
    task = asyncio.create_task(
        surface.act(framed({"type": "type", "text": "hello"}), observation.id)
    )
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel()
    closing = asyncio.create_task(surface.close())
    await asyncio.sleep(0.01)
    assert not task.done() and not closing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await closing
    assert calls[-2:] == [("typed",), ("close",)]
    with pytest.raises(SurfaceError, match="closed"):
        await surface.observe()


@pytest.mark.parametrize("age", [0, -1, 121, float("nan"), float("inf"), True])
def test_observation_age_bound(age, fake_desktop, tmp_path):
    with pytest.raises(SurfaceError):
        ComputerSurface(tmp_path, max_observation_age_s=age)


async def test_evidence_file_tamper_prevents_action(fake_desktop, tmp_path):
    calls, _, _, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    Path(observation.path).write_bytes(b"replaced")
    with pytest.raises(SurfaceError, match="evidence"):
        await surface.act(framed({"type": "click", "x": 1, "y": 1}), observation.id)
    assert not any(call[0] == "open" for call in calls)
    await surface.close()


async def test_capture_uses_unique_private_paths_and_no_legacy_screenshots(
    fake_desktop, monkeypatch, tmp_path
):
    _, _, source, _ = fake_desktop
    paths = []
    commands = []

    async def fake_capture(self, argv):
        commands.append(tuple(argv[:-1]))
        path = Path(argv[-1])
        paths.append(path)
        assert path.parent == tmp_path / "evidence"
        assert path.stat().st_mode & 0o777 == 0o600
        path.write_bytes(source.read_bytes())
        return 0

    monkeypatch.setattr(
        ComputerSurface, "_run_capture_command", fake_capture, raising=False
    )
    surface = ComputerSurface(tmp_path / "evidence")
    first = await surface.observe()
    second = await surface.observe()
    assert first.path != second.path
    assert len(set(paths)) == 2
    assert all(not path.exists() for path in paths)
    assert commands == [("spectacle", "-b", "-n", "-o")] * 2
    await surface.close()
    assert list((tmp_path / "evidence").iterdir()) == []


@pytest.mark.parametrize("sandbox", [False, True])
async def test_capture_fallback_stays_on_configured_target(
    sandbox, fake_desktop, monkeypatch, tmp_path
):
    if sandbox:
        monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":91")
        monkeypatch.setenv("WAYLAND_DISPLAY", "real-wayland")
        monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/real/bus")
        monkeypatch.setenv("JARVIS_SANDBOX_BUS_ADDRESS", "unix:path=/private/bus")
    commands = []

    async def fail_capture(self, argv):
        commands.append(tuple(argv[:-1]))
        Path(argv[-1]).write_bytes(b"partial data")
        return 1

    monkeypatch.setattr(
        ComputerSurface, "_run_capture_command", fail_capture, raising=False
    )
    surface = ComputerSurface(tmp_path / "evidence")
    if sandbox:
        assert surface._capture_environment["DISPLAY"] == ":91"
        assert (
            surface._capture_environment["DBUS_SESSION_BUS_ADDRESS"]
            == "unix:path=/private/bus"
        )
        assert "WAYLAND_DISPLAY" not in surface._capture_environment
    with pytest.raises(SurfaceError, match="capture"):
        await surface.observe()
    expected = [("import", "-window", "root")]
    if not sandbox:
        expected.insert(0, ("spectacle", "-b", "-n", "-o"))
    assert commands == expected
    assert list((tmp_path / "evidence").iterdir()) == []
    await surface.close()


async def test_cancelled_capture_removes_raw_png_before_unlock(
    fake_desktop, monkeypatch, tmp_path
):
    started = asyncio.Event()
    path = None

    async def wait_capture(self, argv):
        nonlocal path
        path = Path(argv[-1])
        path.write_bytes(b"partial image")
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        ComputerSurface, "_run_capture_command", wait_capture, raising=False
    )
    surface = ComputerSurface(tmp_path / "evidence")
    task = asyncio.create_task(surface.observe())
    await asyncio.wait_for(started.wait(), 2)
    assert path is not None and path.exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not path.exists()
    await surface.close()


@pytest.mark.parametrize("stop_kind", ["cancel", "timeout"])
async def test_capture_subprocess_is_killed_and_reaped(
    stop_kind, monkeypatch, tmp_path
):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.delenv("JARVIS_DESKTOP_SANDBOX", raising=False)
    started = asyncio.Event()
    stopped = asyncio.Event()
    calls = []

    class Process:
        pid = 77777777
        returncode = None

        async def wait(self):
            started.set()
            await stopped.wait()
            calls.append("reaped")
            self.returncode = -9
            return -9

        def kill(self):
            calls.append("kill")
            stopped.set()

    async def spawn(*argv, **kwargs):
        assert kwargs["start_new_session"] is True
        assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
        assert kwargs["stdout"] == asyncio.subprocess.DEVNULL
        assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
        assert isinstance(kwargs["env"], dict)
        return Process()

    def kill_group(pid, sig):
        assert pid == Process.pid and sig == signal.SIGKILL
        calls.append("killpg")
        stopped.set()

    monkeypatch.setattr(surface_module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(surface_module.os, "killpg", kill_group)
    monkeypatch.setattr(surface_module, "CAPTURE_TIMEOUT_S", 0.01)
    surface = ComputerSurface(tmp_path / "evidence")
    task = asyncio.create_task(surface._run_capture_command(("fake-capture",)))
    await asyncio.wait_for(started.wait(), 2)
    if stop_kind == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(SurfaceError, match="timed out"):
            await task
    assert "killpg" in calls and "reaped" in calls
    await surface.close()


async def test_pixel_hash_ignores_png_metadata(fake_desktop, tmp_path):
    from PIL.PngImagePlugin import PngInfo

    _, _, source, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    first = await surface.observe()
    metadata = PngInfo()
    metadata.add_text("capture-time", "different every capture")
    Image.new("RGB", (200, 100), "white").save(source, pnginfo=metadata)
    second = await surface.observe()
    assert first.sha256 != second.sha256
    assert first.pixels_sha256 == second.pixels_sha256
    await surface.close()


async def test_missing_sandbox_capture_tool_cannot_use_desktop(
    fake_desktop, monkeypatch, tmp_path
):
    monkeypatch.setenv("JARVIS_DESKTOP_SANDBOX", ":91")
    calls = []

    async def missing(self, argv):
        calls.append(argv[0])
        raise FileNotFoundError("private capture tool missing")

    monkeypatch.setattr(ComputerSurface, "_run_capture_command", missing)
    surface = ComputerSurface(tmp_path / "evidence")
    with pytest.raises(SurfaceError, match="capture"):
        await surface.observe()
    assert calls == ["import"]
    assert list((tmp_path / "evidence").iterdir()) == []
    await surface.close()


async def test_cancel_during_capture_spawn_still_obtains_and_reaps_process(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.delenv("JARVIS_DESKTOP_SANDBOX", raising=False)
    spawning, finish_spawn = asyncio.Event(), asyncio.Event()
    events = []

    class Process:
        pid = 88888888
        returncode = None

        async def wait(self):
            events.append("reaped")
            self.returncode = -9
            return -9

        def kill(self):
            events.append("killed")

    async def spawn(*args, **kwargs):
        spawning.set()
        await finish_spawn.wait()
        return Process()

    def kill_group(pid, sig):
        assert pid == Process.pid and sig == signal.SIGKILL
        events.append("group killed")

    monkeypatch.setattr(surface_module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(surface_module.os, "killpg", kill_group)
    surface = ComputerSurface(tmp_path / "evidence")
    task = asyncio.create_task(surface._run_capture_command(("fake-capture",)))
    await asyncio.wait_for(spawning.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    finish_spawn.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["group killed", "killed", "reaped"]
    await surface.close()


def app_scene(fake_desktop):
    from PIL import ImageDraw

    _, state, path, _ = fake_desktop
    state.update(width=1200, height=800, frame=(100, 100, 900, 600))
    image = Image.new("RGB", (1200, 800), "#111827")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 999, 699), fill="#eeeeee", outline="#444444", width=2)
    draw.rectangle((180, 180, 330, 240), fill="#6688cc", outline="black")
    draw.text((210, 201), "Search", fill="black")
    draw.rectangle((180, 300, 650, 345), fill="white", outline="#3355aa", width=2)
    draw.rectangle((180, 410, 650, 455), fill="white", outline="#666666", width=2)
    image.save(path)
    return image, path


FIELD_REGION = {"x": 145, "y": 370, "width": 410, "height": 70}
FIELD_CLICK = {"type": "click", "x": 200, "y": 400, "region": FIELD_REGION}


async def test_unrelated_clock_and_hud_animation_allow_grounded_click(
    fake_desktop, tmp_path
):
    from PIL import ImageDraw

    calls, _, _, _ = fake_desktop
    image, path = app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    draw = ImageDraw.Draw(image)
    draw.text((1080, 20), "12:34:56", fill="white")
    for x in range(15, 100, 15):
        for y in range(40, 750, 80):
            draw.ellipse((x, y, x + 5, y + 5), fill="#22ddcc")
    image.save(path)
    await surface.act(FIELD_CLICK, observation.id)
    assert ("click", "left") in calls
    await surface.close()


@pytest.mark.parametrize("changed", ["window_id", "focus_id", "frame"])
async def test_identical_pixels_changed_foreground_or_geometry_block_input(
    fake_desktop, tmp_path, changed
):
    calls, state, _, _ = fake_desktop
    app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    state[changed] = (110, 100, 900, 600) if changed == "frame" else "other-window"
    with pytest.raises(SurfaceError, match=r"foreground|focus|window|geometry"):
        await surface.act(FIELD_CLICK, observation.id)
    assert not any(call[0] in {"click", "type"} for call in calls)
    await surface.close()


async def test_focus_change_during_capture_does_not_publish_observation(
    fake_desktop, tmp_path, monkeypatch
):
    _, state, _, _ = fake_desktop
    app_scene(fake_desktop)
    original = ComputerSurface._focus_snapshot
    count = 0

    async def changed(self):
        nonlocal count
        count += 1
        if count == 2:
            state["window_id"] = "different"
        return await original(self)

    monkeypatch.setattr(ComputerSurface, "_focus_snapshot", changed)
    surface = ComputerSurface(tmp_path / "evidence")
    with pytest.raises(SurfaceError, match=r"foreground|focus|window"):
        await surface.observe()
    assert not list((tmp_path / "evidence").glob("computer-observation-*"))
    await surface.close()


async def test_keyboard_requires_own_click_anchor_and_exact_region(
    fake_desktop, tmp_path
):
    calls, _, _, _ = fake_desktop
    app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    with pytest.raises(SurfaceError, match="anchor"):
        await surface.act(
            {"type": "type", "text": "hi", "region": FIELD_REGION}, observation.id
        )
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    with pytest.raises(SurfaceError, match="region"):
        await surface.act(
            {"type": "type", "text": "hi", "region": {**FIELD_REGION, "y": 500}},
            observation.id,
        )
    assert not any(call[0] == "type" for call in calls)
    await surface.close()


async def test_temporally_verified_blink_allows_typing(
    fake_desktop, tmp_path, monkeypatch
):
    from PIL import ImageDraw

    calls, _, _, _ = fake_desktop
    base, path = app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    caret = base.copy()
    ImageDraw.Draw(caret).rectangle((250, 310, 251, 330), fill="black")
    caret.save(path)
    original = ComputerSurface._run_capture_command
    samples = 0

    async def blink(self, argv):
        nonlocal samples
        samples += 1
        if samples >= 2:
            base.save(path)
        return await original(self, argv)

    monkeypatch.setattr(ComputerSurface, "_run_capture_command", blink)
    await surface.act(
        {"type": "type", "text": "physics", "region": FIELD_REGION}, observation.id
    )
    assert ("type", "physics") in calls and samples >= 2
    await surface.close()


async def test_static_narrow_glyph_is_not_accepted_as_a_blink(
    fake_desktop, tmp_path, monkeypatch
):
    from PIL import ImageDraw

    calls, _, _, _ = fake_desktop
    base, path = app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    ImageDraw.Draw(base).rectangle((250, 310, 251, 330), fill="black")
    base.save(path)
    monkeypatch.setattr(surface_module, "CARET_VERIFY_SECONDS", 0.02)
    monkeypatch.setattr(surface_module, "CARET_SAMPLE_SECONDS", 0.005)
    with pytest.raises(SceneChangedError):
        await surface.act(
            {"type": "type", "text": "physics", "region": FIELD_REGION}, observation.id
        )
    assert not any(call[0] == "type" for call in calls)
    await surface.close()


async def test_new_caret_in_other_field_blocks_typing(fake_desktop, tmp_path):
    from PIL import ImageDraw

    calls, _, _, _ = fake_desktop
    base, path = app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    ImageDraw.Draw(base).rectangle((250, 420, 251, 440), fill="black")
    base.save(path)
    with pytest.raises(SceneChangedError):
        await surface.act(
            {"type": "type", "text": "physics", "region": FIELD_REGION}, observation.id
        )
    assert not any(call[0] == "type" for call in calls)
    await surface.close()


async def test_tab_invalidates_keyboard_anchor(fake_desktop, tmp_path):
    app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    observation = await surface.act(
        {"type": "press", "key": "Tab", "region": FIELD_REGION}, observation.id
    )
    with pytest.raises(SurfaceError, match="anchor"):
        await surface.act(
            {"type": "type", "text": "physics", "region": FIELD_REGION}, observation.id
        )
    await surface.close()


async def test_focus_change_during_caret_sampling_blocks_input(
    fake_desktop, tmp_path, monkeypatch
):
    from PIL import ImageDraw

    calls, state, _, _ = fake_desktop
    base, path = app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    caret = base.copy()
    ImageDraw.Draw(caret).rectangle((250, 310, 251, 330), fill="black")
    caret.save(path)
    original = ComputerSurface._run_capture_command
    samples = 0

    async def moved_focus(self, argv):
        nonlocal samples
        samples += 1
        if samples == 2:
            base.save(path)
            state["focus_id"] = "different-field"
        return await original(self, argv)

    monkeypatch.setattr(ComputerSurface, "_run_capture_command", moved_focus)
    with pytest.raises(SurfaceError, match="focus"):
        await surface.act(
            {"type": "type", "text": "physics", "region": FIELD_REGION}, observation.id
        )
    assert samples == 2 and not any(call[0] == "type" for call in calls)
    assert surface.keyboard_anchor is None
    await surface.close()


async def test_cancellation_during_caret_sampling_never_types(
    fake_desktop, tmp_path, monkeypatch
):
    from PIL import ImageDraw

    calls, _, _, _ = fake_desktop
    base, path = app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    ImageDraw.Draw(base).rectangle((250, 310, 251, 330), fill="black")
    base.save(path)
    original = ComputerSurface._run_capture_command
    sampling = asyncio.Event()
    samples = 0

    async def capture(self, argv):
        nonlocal samples
        samples += 1
        if samples == 2:
            sampling.set()
            await asyncio.Event().wait()
        return await original(self, argv)

    monkeypatch.setattr(ComputerSurface, "_run_capture_command", capture)
    task = asyncio.create_task(
        surface.act(
            {"type": "type", "text": "physics", "region": FIELD_REGION}, observation.id
        )
    )
    await asyncio.wait_for(sampling.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not any(call[0] == "type" for call in calls)
    assert ("close",) in calls and surface.keyboard_anchor is None
    await surface.close()
    assert not list((tmp_path / "evidence").iterdir())


async def test_final_focus_check_blocks_last_moment_switch(
    fake_desktop, tmp_path, monkeypatch
):
    calls, state, _, _ = fake_desktop
    app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    original = surface._tools.confirm_desktop_action

    async def arm_then_switch(*args, **kwargs):
        await original(*args, **kwargs)
        state["window_id"] = "new-window"

    monkeypatch.setattr(surface._tools, "confirm_desktop_action", arm_then_switch)
    with pytest.raises(SurfaceError, match="foreground"):
        await surface.act(FIELD_CLICK, observation.id)
    assert not any(call[0] == "click" for call in calls)
    await surface.close()


async def test_missing_focus_metadata_prevents_capture(
    fake_desktop, tmp_path, monkeypatch
):
    calls, _, _, _ = fake_desktop

    async def missing(self):
        raise SurfaceError("Foreground metadata is unavailable.")

    monkeypatch.setattr(ComputerSurface, "_focus_snapshot", missing)
    surface = ComputerSurface(tmp_path / "evidence")
    with pytest.raises(SurfaceError, match="metadata"):
        await surface.observe()
    assert not calls
    await surface.close()


async def test_overlay_covering_target_prevents_input(fake_desktop, tmp_path):
    calls, state, _, _ = fake_desktop
    app_scene(fake_desktop)
    state["overlays"] = (("popup", 220, 310, 200, 100),)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    with pytest.raises(SurfaceError, match="covers"):
        await surface.act(FIELD_CLICK, observation.id)
    assert not any(call[0] == "click" for call in calls)
    await surface.close()


async def test_changing_caret_pattern_before_reversion_is_rejected(
    fake_desktop, tmp_path, monkeypatch
):
    from PIL import ImageDraw

    calls, _, _, _ = fake_desktop
    base, path = app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    caret = base.copy()
    ImageDraw.Draw(caret).rectangle((250, 310, 251, 330), fill="black")
    caret.save(path)
    original = ComputerSurface._run_capture_command
    samples = 0

    async def color_shift(self, argv):
        nonlocal samples
        samples += 1
        if samples == 2:
            ImageDraw.Draw(caret).rectangle((250, 310, 251, 330), fill="red")
            caret.save(path)
        elif samples > 2:
            base.save(path)
        return await original(self, argv)

    monkeypatch.setattr(ComputerSurface, "_run_capture_command", color_shift)
    with pytest.raises(SceneChangedError):
        await surface.act(
            {"type": "type", "text": "physics", "region": FIELD_REGION}, observation.id
        )
    assert not any(call[0] == "type" for call in calls)
    await surface.close()


@pytest.mark.parametrize(
    "action",
    [
        {"type": "move", "x": 200, "y": 400, "region": FIELD_REGION},
        {"type": "type", "text": "physics", "region": FIELD_REGION},
    ],
)
async def test_input_consumes_keyboard_anchor_before_new_baseline(
    fake_desktop, tmp_path, action
):
    app_scene(fake_desktop)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    observation = await surface.act(FIELD_CLICK, observation.id)
    observation = await surface.act(action, observation.id)
    assert surface.keyboard_anchor is None
    with pytest.raises(SurfaceError, match="anchor"):
        await surface.act(
            {"type": "type", "text": "second field", "region": FIELD_REGION},
            observation.id,
        )
    await surface.close()


async def test_target_change_during_geometry_query_is_seen(
    fake_desktop, tmp_path, monkeypatch
):
    calls, _, source, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()

    async def geometry():
        Image.new("RGB", (200, 100), "black").save(source)
        return 200, 100

    monkeypatch.setattr(desktop, "_desktop_size", geometry)
    with pytest.raises(SceneChangedError):
        await surface.act(framed({"type": "click", "x": 20, "y": 20}), observation.id)
    assert not any(call[0] == "click" for call in calls)
    await surface.close()


async def test_late_focus_query_cannot_age_pixels_without_bound(
    fake_desktop, tmp_path, monkeypatch
):
    calls, _, _, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    original = surface._tools.confirm_desktop_action

    async def delayed(*args, **kwargs):
        await original(*args, **kwargs)
        await asyncio.sleep(0.02)

    monkeypatch.setattr(surface._tools, "confirm_desktop_action", delayed)
    monkeypatch.setattr(
        surface_module, "MAX_INPUT_EVIDENCE_AGE_S", 0.005, raising=False
    )
    with pytest.raises(SceneChangedError, match="aged"):
        await surface.act(framed({"type": "click", "x": 20, "y": 20}), observation.id)
    assert not any(call[0] == "click" for call in calls)
    await surface.close()


async def test_threadpool_delay_cannot_age_pixels_before_injection(
    fake_desktop, tmp_path, monkeypatch
):
    calls, _, _, _ = fake_desktop
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    original = ComputerSurface._settled_thread

    async def delayed(function, cancelled=None):
        if cancelled is not None:
            await asyncio.sleep(0.07)
        return await original(function, cancelled)

    monkeypatch.setattr(ComputerSurface, "_settled_thread", staticmethod(delayed))
    monkeypatch.setattr(surface_module, "MAX_INPUT_EVIDENCE_AGE_S", 0.05)
    with pytest.raises(SceneChangedError, match="aged"):
        await surface.act(framed({"type": "click", "x": 20, "y": 20}), observation.id)
    assert not any(call[0] in {"move", "click"} for call in calls)
    await surface.close()


@pytest.mark.parametrize(
    "action",
    [
        {"type": "click", "x": 20, "y": 20},
        {"type": "scroll", "direction": "down", "times": 2},
    ],
)
async def test_slow_pointer_move_does_not_allow_aged_click_or_scroll(
    fake_desktop, tmp_path, monkeypatch, action
):
    import time

    calls, _, _, input_type = fake_desktop
    original = input_type.move

    def slow_move(self, *args):
        original(self, *args)
        time.sleep(0.07)

    monkeypatch.setattr(input_type, "move", slow_move)
    monkeypatch.setattr(surface_module, "MAX_INPUT_EVIDENCE_AGE_S", 0.05)
    surface = ComputerSurface(tmp_path / "evidence")
    observation = await surface.observe()
    with pytest.raises(SceneChangedError, match="aged"):
        await surface.act(framed(action), observation.id)
    assert any(call[0] == "move" for call in calls)
    assert not any(call[0] in {"click", "scroll"} for call in calls)
    await surface.close()
