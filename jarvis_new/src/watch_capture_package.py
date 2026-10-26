"""Explicit owner-local packaging for the dedicated native screenshot helper.

No install, permission change, cache refresh or subprocess runs on import.
This module is never an assistant/model tool. Run its CLI only after reviewing
the compiled helper and exact desktop entitlement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

IDENTITY = "dev.jarvis.watchcapture"
TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "native/window-capture/dev.jarvis.watchcapture.desktop.in"
)


def _paths(prefix):
    prefix = Path(prefix).absolute()
    return (
        prefix / "libexec/jarvis-watch-capture",
        prefix / f"share/applications/{IDENTITY}.desktop",
        prefix / "libexec/.jarvis-watch-capture-package.json",
    )


def _safe_parents(path):
    for parent in reversed(path.absolute().parents):
        if parent.is_symlink():
            raise ValueError("Package paths cannot contain symlink directories")
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise ValueError(
            "Package directory must belong to the owner and not be writable by others"
        )


def _read_owned(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or info.st_nlink != 1
        ):
            raise ValueError("Package input must be a regular owner file")
        with os.fdopen(fd, "rb", closefd=False) as source:
            return source.read()
    finally:
        os.close(fd)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _desktop(helper):
    # Desktop Exec grammar, not a shell command. Reject format-code/control input.
    raw = str(helper)
    if any(ord(char) < 32 for char in raw) or "%" in raw:
        raise ValueError("Unsupported helper executable path")
    escaped = (
        raw.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("`", "\\`")
        .replace("$", "\\$")
    )
    return TEMPLATE.read_text().replace("@HELPER_EXEC@", f'"{escaped}"').encode()


def install(binary, prefix=None):
    binary = Path(binary).absolute()
    if binary.name != "jarvis-watch-capture" or binary.is_symlink():
        raise ValueError("Review the dedicated jarvis-watch-capture executable first")
    payload = _read_owned(binary)
    if not payload.startswith(b"\x7fELF") or not os.access(binary, os.X_OK):
        raise ValueError("Dedicated compiled ELF executable is required")
    helper, entry, manifest = _paths(prefix or Path.home() / ".local")
    for path in (helper, entry, manifest):
        _safe_parents(path)
        if path.exists() or path.is_symlink():
            raise ValueError(
                "Package file already exists; inspect/remove the prior package first"
            )
    desktop = _desktop(helper)
    record = json.dumps(
        {
            "version": 1,
            "identity": IDENTITY,
            "helper_sha256": _digest(payload),
            "desktop_sha256": _digest(desktop),
        },
        sort_keys=True,
    ).encode()
    created = []
    try:
        for path, data, mode in (
            (helper, payload, 0o700),
            (entry, desktop, 0o600),
            (manifest, record, 0o600),
        ):
            fd = os.open(
                path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode
            )
            created.append(path)
            with os.fdopen(fd, "wb") as destination:
                destination.write(data)
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return {
        "helper": str(helper),
        "desktop_entry": str(entry),
        "identity": IDENTITY,
        "cache_refresh_required": True,
    }


def remove(prefix=None):
    helper, entry, manifest = _paths(prefix or Path.home() / ".local")
    if not any(
        path.exists() or path.is_symlink() for path in (helper, entry, manifest)
    ):
        return {"removed": True, "cache_refresh_required": True}
    for path in (helper, entry, manifest):
        _safe_parents(path)
    record = json.loads(_read_owned(manifest))
    if (
        record.get("version") != 1
        or record.get("identity") != IDENTITY
        or _digest(_read_owned(helper)) != record.get("helper_sha256")
        or _digest(_read_owned(entry)) != record.get("desktop_sha256")
    ):
        raise ValueError(
            "Installed package identity or contents changed; inspect manually"
        )
    for path in (entry, helper, manifest):
        path.unlink()
    return {"removed": True, "cache_refresh_required": True}


def installed_helper(prefix=None):
    """Read-only discovery; an unverified/missing package never selects native."""
    helper, entry, manifest = _paths(prefix or Path.home() / ".local")
    try:
        if any(
            parent.is_symlink()
            for path in (helper, entry, manifest)
            for parent in path.parents
        ):
            return None
        if manifest.stat().st_size > 8192:
            return None
        record = json.loads(_read_owned(manifest))
        if (
            set(record) != {"version", "identity", "helper_sha256", "desktop_sha256"}
            or record["version"] != 1
            or record["identity"] != IDENTITY
        ):
            return None
        payload, desktop = _read_owned(helper), _read_owned(entry)
        if (
            not payload.startswith(b"\x7fELF")
            or not os.access(helper, os.X_OK)
            or _digest(payload) != record["helper_sha256"]
            or _digest(desktop) != record["desktop_sha256"]
            or desktop != _desktop(helper)
        ):
            return None
        return str(helper)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "remove"))
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--prefix", type=Path)
    args = parser.parse_args()
    if args.action == "install" and args.binary is None:
        parser.error("install requires the reviewed --binary path")
    print(
        json.dumps(
            install(args.binary, args.prefix)
            if args.action == "install"
            else remove(args.prefix),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
