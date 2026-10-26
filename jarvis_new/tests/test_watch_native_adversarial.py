"""Independent native packaging/adapter negatives; no session bus or capture."""

import json
import subprocess
from pathlib import Path

import pytest

import watch_capture_package as package
import watch_native_capture as native
from screen_understanding import ScreenError

TARGET = ("12345678-1234-5678-9abc-123456789abc", "owned-fixture", 321)


def metadata(**changes):
    return json.dumps(
        {
            "uuid": TARGET[0],
            "app": TARGET[1],
            "pid": TARGET[2],
            "width": 200,
            "height": 100,
            "caption": "Owned fixture",
            **changes,
        }
    ).encode()


def helper(tmp_path, content=b"\x7fELFowned-fixture"):
    path = tmp_path / "jarvis-watch-capture"
    path.write_bytes(content)
    path.chmod(0o700)
    return path


def test_interpreter_script_cannot_receive_compiled_helper_entitlement(tmp_path):
    binary = helper(tmp_path, b"#!/usr/bin/python3\nprint('fixture')\n")
    prefix = tmp_path / "install"
    with pytest.raises(ValueError, match="ELF"):
        package.install(binary, prefix)
    assert not prefix.exists()


def test_package_cannot_install_through_symlinked_libexec(tmp_path):
    binary = helper(tmp_path)
    prefix = tmp_path / "install"
    prefix.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (prefix / "libexec").symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        package.install(binary, prefix)
    assert not list(foreign.iterdir())


def test_package_refuses_directory_other_users_can_write(tmp_path):
    binary = helper(tmp_path)
    prefix = tmp_path / "install"
    destination = prefix / "libexec"
    destination.mkdir(parents=True)
    destination.chmod(0o777)
    with pytest.raises(ValueError, match="writable"):
        package.install(binary, prefix)
    assert not list(destination.iterdir())


def test_percent_format_codes_cannot_enter_executable_identity(tmp_path):
    binary = helper(tmp_path)
    prefix = tmp_path / "bad-%f-path"
    with pytest.raises(ValueError, match="executable path"):
        package.install(binary, prefix)
    assert not (prefix / "libexec/jarvis-watch-capture").exists()


def test_changed_native_binary_does_not_remove_foreign_package_contents(tmp_path):
    result = package.install(helper(tmp_path), tmp_path / "install")
    installed = Path(result["helper"])
    installed.write_bytes(b"\x7fELFchanged-owner-program")
    with pytest.raises(ValueError, match="contents changed"):
        package.remove(tmp_path / "install")
    assert installed.read_bytes() == b"\x7fELFchanged-owner-program"
    assert Path(result["desktop_entry"]).exists()


@pytest.mark.parametrize(
    "changes",
    [
        {"uuid": "abcdef12-1234-5678-90ab-1234567890ab"},
        {"pid": 999},
        {"app": "other-app"},
        {"pid": True},
        {"width": True},
        {"height": 1.5},
        {"width": 16385},
        {"width": 16384, "height": 16384},
        {"caption": ["secret"]},
    ],
)
def test_helper_metadata_cannot_substitute_target_or_unbounded_geometry(changes):
    with pytest.raises(ScreenError):
        native.parse_metadata(metadata(**changes), TARGET)


def test_metadata_size_limit_rejects_before_interpretation():
    with pytest.raises(ScreenError, match="metadata"):
        native.parse_metadata(b" " * (native.MAX_METADATA + 1), TARGET)


def test_target_inspection_never_requests_capture(tmp_path):
    commands = []

    def runner(command, **options):
        commands.append(command)
        assert options["timeout"] <= 1.5
        assert options["stderr"] == subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0, metadata(), b"")

    result = native.inspect_target(TARGET, helper(tmp_path), runner=runner)
    assert result["id"] == TARGET[0]
    assert len(commands) == 1
    assert commands[0][1] == "--inspect"


def test_untrusted_script_helper_rejected_without_execution(tmp_path):
    binary = helper(tmp_path, b"#!/bin/sh\nexit 1\n")
    with pytest.raises(ScreenError, match="ELF"):
        native.inspect_target(
            TARGET, binary, runner=lambda *_a, **_k: pytest.fail("untrusted execution")
        )


@pytest.mark.asyncio
async def test_wrong_enrolled_target_never_spawns_capture(tmp_path):
    async def runner(*_):
        pytest.fail("capture requested after target substitution")

    backend = native.NativeWindowBackend(TARGET, helper=helper(tmp_path), run=runner)
    with pytest.raises(ScreenError, match="changed"):
        await backend.capture(
            ((TARGET[0], "other-app", TARGET[2]), (200, 100)), lambda: None
        )


@pytest.mark.asyncio
async def test_native_capture_cancellation_check_prevents_any_helper_call(tmp_path):
    async def runner(*_):
        pytest.fail("capture started after stop")

    backend = native.NativeWindowBackend(TARGET, helper=helper(tmp_path), run=runner)

    def stopped():
        raise ScreenError("permission revoked")

    with pytest.raises(ScreenError, match="revoked"):
        await backend.capture((TARGET, (200, 100)), stopped)


@pytest.mark.asyncio
async def test_invalid_native_pixels_have_no_desktop_capture_fallback(tmp_path):
    commands = []

    async def runner(command, timeout):
        commands.append(command)
        return b"raw secret malformed pixels"

    backend = native.NativeWindowBackend(TARGET, helper=helper(tmp_path), run=runner)
    with pytest.raises(ScreenError, match="image"):
        await backend.capture((TARGET, (200, 100)), lambda: None)
    assert len(commands) == 1
    assert commands[0][1] == "--capture"
    assert "secret" not in str(backend.target)


@pytest.mark.asyncio
async def test_revocation_during_native_capture_never_returns_pixels(tmp_path):
    authority = [True]

    async def runner(command, timeout):
        authority[0] = False
        return b"private pixels"

    backend = native.NativeWindowBackend(TARGET, helper=helper(tmp_path), run=runner)

    def check():
        if not authority[0]:
            raise ScreenError("permission revoked")

    with pytest.raises(ScreenError, match="revoked") as failure:
        await backend.capture((TARGET, (200, 100)), check)
    assert "private pixels" not in str(failure.value)


def test_native_probe_error_never_exposes_helper_output(tmp_path):
    def runner(command, **options):
        return subprocess.CompletedProcess(
            command, 1, b"private malformed stdout", b"provider secret"
        )

    with pytest.raises(ScreenError) as failure:
        native.inspect_target(TARGET, helper(tmp_path), runner=runner)
    assert "private" not in str(failure.value)
    assert "secret" not in str(failure.value)


def test_helper_in_directory_writable_by_others_is_not_executed(tmp_path):
    directory = tmp_path / "untrusted"
    directory.mkdir()
    binary = helper(directory)
    directory.chmod(0o777)
    with pytest.raises(ScreenError, match="directory"):
        native.inspect_target(
            TARGET,
            binary,
            runner=lambda *_a, **_k: pytest.fail("untrusted helper launched"),
        )


def test_helper_through_symlink_parent_is_not_executed(tmp_path):
    directory = tmp_path / "owned"
    directory.mkdir()
    helper(directory)
    linked = tmp_path / "alias"
    linked.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ScreenError, match="directory"):
        native.inspect_target(
            TARGET,
            linked / "jarvis-watch-capture",
            runner=lambda *_a, **_k: pytest.fail("symlink helper launched"),
        )
