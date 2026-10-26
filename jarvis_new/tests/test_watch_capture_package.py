from pathlib import Path

import pytest

from watch_capture_package import install, installed_helper, remove


def binary(tmp_path):
    target = tmp_path / "build" / "jarvis-watch-capture"
    target.parent.mkdir()
    target.write_bytes(b"\x7fELF" + b"synthetic-build-fixture")
    target.chmod(0o700)
    return target


def test_owner_local_install_grants_only_dedicated_binary_and_removes(tmp_path):
    compiled = binary(tmp_path)
    prefix = tmp_path / "owner-local"
    result = install(compiled, prefix)
    helper = Path(result["helper"])
    entry = Path(result["desktop_entry"])
    assert helper.read_bytes() == compiled.read_bytes()
    assert helper.stat().st_mode & 0o777 == 0o700
    text = entry.read_text()
    assert f'Exec="{helper}"' in text
    assert "X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2" in text
    assert "python" not in text and "spectacle" not in text.lower()
    assert installed_helper(prefix) == str(helper)
    remove(prefix)
    assert not helper.exists() and not entry.exists()
    assert installed_helper(prefix) is None


def test_install_refuses_overwrite_or_symlink(tmp_path):
    compiled = binary(tmp_path)
    prefix = tmp_path / "owner-local"
    install(compiled, prefix)
    with pytest.raises(ValueError, match="already exists"):
        install(compiled, prefix)
    destination = tmp_path / "other"
    destination.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(destination, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        install(compiled, alias)


def test_remove_refuses_modified_unrelated_entry(tmp_path):
    compiled = binary(tmp_path)
    prefix = tmp_path / "owner-local"
    result = install(compiled, prefix)
    Path(result["desktop_entry"]).write_text("[Desktop Entry]\nExec=other\n")
    with pytest.raises(ValueError, match="package identity"):
        remove(prefix)
    assert Path(result["helper"]).exists()
    assert installed_helper(prefix) is None
