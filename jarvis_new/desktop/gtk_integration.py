#!/usr/bin/env python3
"""Reversible GTK selection across KDE's GTK bridge, GTK2 and XSettings.

No previews, window creation, compositor restarts, or blanket GTK4 CSS injection.
The parent installer owns ordinary GTK settings.ini and ~/.gtkrc-2.0. This
module handles the effective alternate GTK2_RC_FILES path and old overrides.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess

HOME = Path.home()
CFG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
DATA = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
SCHEMA = "org.gnome.desktop.interface"
GTK2_VALUES = {
    "gtk-theme-name": "StarkOS",
    "gtk-icon-theme-name": "stark-os",
    "gtk-cursor-theme-name": "stark-os-cursors",
    "gtk-font-name": "Rajdhani Medium 11",
}
XSETTINGS_VALUES = {
    "Net/ThemeName": "StarkOS",
    "Net/IconThemeName": "stark-os",
    "Gtk/CursorThemeName": "stark-os-cursors",
    "Gtk/FontName": "Rajdhani Medium 11",
}
GSETTINGS_VALUES = {
    "gtk-theme": "StarkOS",
    "icon-theme": "stark-os",
    "cursor-theme": "stark-os-cursors",
    "font-name": "Rajdhani Medium 11",
    "document-font-name": "Rajdhani Medium 11",
    "monospace-font-name": "Share Tech Mono 10",
    "color-scheme": "prefer-dark",
}
# Match only the exact previously installed managed block, preserving all other
# user CSS and KDE's native colors.css import. Unknown blocks are not removed.
LEGACY_JARVIS_CSS = """/* Jarvis theme begin */
@define-color theme_bg_color #102636;
@define-color theme_fg_color #e2f3fa;
@define-color theme_base_color #071522;
@define-color theme_text_color #e2f3fa;
@define-color theme_selected_bg_color #66d9ef;
@define-color theme_selected_fg_color #071522;
@define-color accent_bg_color #66d9ef;
@define-color accent_fg_color #071522;
@define-color window_bg_color #071522;
@define-color window_fg_color #e2f3fa;
@define-color headerbar_bg_color #102636;
@define-color headerbar_fg_color #e2f3fa;
window, dialog { background-color: #071522; color: #e2f3fa; }
headerbar, menubar, toolbar { background: #102636; color: #e2f3fa; }
entry, textview, treeview { background-color: #071522; color: #e2f3fa; }
button:checked, row:selected { background-color: #66d9ef; color: #071522; }
button:focus, entry:focus { outline-color: #66d9ef; }
/* Jarvis theme end */"""
THEME_INCLUDE = re.compile(
    r'^\s*include\s+["\'][^"\'\n]*/themes/[^/"\'\n]+/gtk-2\.0/gtkrc["\']\s*$', re.M
)


def configure_paths(home: Path, config: Path, data: Path) -> None:
    global HOME, CFG, DATA
    HOME, CFG, DATA = Path(home), Path(config), Path(data)


def live_session() -> bool:
    return HOME.resolve() == Path.home().resolve() and bool(
        os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    )


def checked_path(path: Path) -> Path:
    # Never follow environment-selected GTK configuration into a system file or
    # another user's directory. The same check protects restore manifests.
    resolved = path.expanduser().resolve()
    if not resolved.is_relative_to(HOME.resolve()):
        raise ValueError(
            f"GTK configuration path is outside the configured home: {path}"
        )
    return resolved


def gtk2_path() -> Path | None:
    candidates = os.environ.get("GTK2_RC_FILES", str(HOME / ".gtkrc-2.0")).split(":")
    for name in reversed(candidates):
        if not name:
            continue
        try:
            path = checked_path(Path(name))
        except ValueError:
            continue
        # integration.py already owns the fallback file's managed block.
        if path == (HOME / ".gtkrc-2.0").resolve():
            return None
        parent = path.parent
        while not parent.exists() and parent != HOME:
            parent = parent.parent
        if os.access(path if path.exists() else parent, os.W_OK):
            return path
    return None


def file_inventory() -> list[tuple[str, Path]]:
    result = [("xsettings", checked_path(CFG / "xsettingsd/xsettingsd.conf"))]
    target = gtk2_path()
    if target is not None:
        result.append(("gtk2", target))
    result.extend(
        ("css", checked_path(CFG / f"gtk-{version}/gtk.css"))
        for version in ("3.0", "4.0")
    )
    return result


def key_pattern(keys: dict[str, str], kind: str) -> re.Pattern:
    separator = r"\s*=\s*" if kind == "gtk2" else r"\s+"
    return re.compile(
        r"^[ \t]*(?:"
        + "|".join(map(re.escape, keys))
        + ")"
        + separator
        + r"[^\n]*(?:\n|$)",
        re.M,
    )


def transform(kind: str, text: str) -> str:
    if kind == "css":
        return text.replace(LEGACY_JARVIS_CSS, "")
    values = GTK2_VALUES if kind == "gtk2" else XSETTINGS_VALUES
    clean = key_pattern(values, kind).sub("", text)
    if kind == "gtk2":
        clean = THEME_INCLUDE.sub("", clean)
    separator = "=" if kind == "gtk2" else " "
    addition = "".join(
        f"{key}{separator}{json.dumps(value)}\n" for key, value in values.items()
    )
    if kind == "gtk2":
        addition = (
            f"include {json.dumps(str(DATA / 'themes/StarkOS/gtk-2.0/gtkrc'))}\n"
            + addition
        )
    return clean + ("\n" if clean and not clean.endswith("\n") else "") + addition


def save_text(path: Path, text: str) -> None:
    checked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


GSETTINGS_HELPER = r"""
import json, sys
from gi.repository import Gio, GLib
request = json.load(sys.stdin)
source = Gio.SettingsSchemaSource.get_default()
schema = source.lookup(request["schema"], True)
if schema is None:
    raise RuntimeError("Missing GSettings schema: " + request["schema"])
settings = Gio.Settings.new_full(schema, None, None)
keys = request["values"]
if request["action"] == "read":
    result = {}
    for key in keys:
        user = settings.get_user_value(key)
        result[key] = {"user": None if user is None else user.print_(True),
                       "value": settings.get_value(key).unpack()}
else:
    settings.delay()
    for key, value in keys.items():
        if not settings.is_writable(key):
            raise RuntimeError("Locked GSettings preference: " + key)
        if request["action"] == "restore":
            if value is None:
                settings.reset(key)
            elif not settings.set_value(key, GLib.Variant.parse(None, value, None, None)):
                raise RuntimeError("Cannot restore GSettings preference: " + key)
        elif not settings.set_string(key, value):
            raise RuntimeError("Cannot set GSettings preference: " + key)
    settings.apply()
    Gio.Settings.sync()
    result = {}
print(json.dumps(result))
"""


def gsettings(action: str, values: dict | None = None) -> dict:
    isolated_backend = os.environ.get("GSETTINGS_BACKEND") == "keyfile"
    if not isolated_backend and (
        HOME.resolve() != Path.home().resolve()
        or (action != "read" and not live_session())
    ):
        return {}
    environment = dict(os.environ, XDG_CONFIG_HOME=str(CFG), XDG_DATA_HOME=str(DATA))
    result = subprocess.run(
        ["/usr/bin/python3", "-c", GSETTINGS_HELPER],
        input=json.dumps(
            {
                "action": action,
                "schema": SCHEMA,
                "values": GSETTINGS_VALUES if values is None else values,
            }
        ),
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    )
    return json.loads(result.stdout)


def native_theme(theme: str | None = None) -> str | None:
    if not live_session():
        return None
    executable = next(
        (
            shutil.which(name)
            for name in ("qdbus-qt6", "qdbus6", "qdbus")
            if shutil.which(name)
        ),
        None,
    )
    if not executable:
        raise RuntimeError("KDE GTK theme synchronization requires qdbus")
    method = (
        "org.kde.GtkConfig.gtkTheme"
        if theme is None
        else "org.kde.GtkConfig.setGtkTheme"
    )
    arguments = [executable, "org.kde.kded6", "/modules/gtkconfig", method]
    if theme is not None:
        arguments.append(theme)
    return subprocess.check_output(arguments, text=True, timeout=15).strip()


def reload_xsettings() -> None:
    if not live_session():
        return
    # Reload an existing same-user daemon only; never start one or touch KWin.
    result = subprocess.run(
        ["pgrep", "-u", str(os.getuid()), "-x", "xsettingsd"],
        capture_output=True,
        text=True,
    )
    for value in result.stdout.split():
        pid = int(value)
        try:
            process = Path("/proc") / value
            if (
                process.stat().st_uid == os.getuid()
                and (process / "comm").read_text().strip() == "xsettingsd"
            ):
                os.kill(pid, signal.SIGHUP)
        except (ProcessLookupError, FileNotFoundError):
            pass


def backup(root: Path) -> None:
    files = []
    for kind, path in file_inventory():
        original = path.read_text() if path.exists() else ""
        files.append(
            {
                "kind": kind,
                "path": str(path),
                "existed": path.exists(),
                "original": original,
                "applied": transform(kind, original),
            }
        )
    state = {
        "version": 1,
        "files": files,
        "gsettings": gsettings("read"),
        "native_theme": native_theme(),
    }
    (root / "gtk-integration.json").write_text(json.dumps(state, indent=2) + "\n")


def apply() -> None:
    # Let KDE update its supported theme selection first. Custom owned values
    # then converge its GTK2, XSettings and GSettings transports consistently.
    native_theme("StarkOS")
    gsettings("apply")
    for kind, path in file_inventory():
        original = path.read_text() if path.exists() else ""
        updated = transform(kind, original)
        if updated != original:
            save_text(path, updated)
    reload_xsettings()


def restore_text(record: dict, current: str) -> str:
    original, applied, kind = record["original"], record["applied"], record["kind"]
    if current == applied or current == original:
        return original
    if kind == "css":
        if LEGACY_JARVIS_CSS in original and LEGACY_JARVIS_CSS not in current:
            return (
                current
                + ("\n" if current and not current.endswith("\n") else "")
                + LEGACY_JARVIS_CSS
            )
        return current
    values = GTK2_VALUES if kind == "gtk2" else XSETTINGS_VALUES
    matcher = key_pattern(values, kind)
    owned = matcher.findall(original)
    clean = matcher.sub("", current)
    if kind == "gtk2":
        owned = THEME_INCLUDE.findall(original) + owned
        clean = THEME_INCLUDE.sub("", clean)
    return (
        clean
        + ("\n" if clean and not clean.endswith("\n") else "")
        + "".join(line.rstrip("\n") + "\n" for line in owned)
    )


def restore(root: Path) -> None:
    path = root / "gtk-integration.json"
    if not path.exists():
        return
    saved = json.loads(path.read_text())
    if saved.get("native_theme"):
        native_theme(saved["native_theme"])
    gsettings(
        "restore", {key: value["user"] for key, value in saved["gsettings"].items()}
    )
    for record in saved["files"]:
        target = checked_path(Path(record["path"]))
        current = target.read_text() if target.exists() else ""
        restored = restore_text(record, current)
        if not record["existed"] and not restored:
            target.unlink(missing_ok=True)
        elif restored != current:
            save_text(target, restored)
    reload_xsettings()


def equivalent(actual: str, expected: str, key: str) -> bool:
    if key in (
        "gtk-font-name",
        "Gtk/FontName",
        "font-name",
        "document-font-name",
        "monospace-font-name",
    ):
        return actual.replace(", ", " ") == expected
    return actual == expected


def verify() -> None:
    errors = []
    for kind, path in file_inventory():
        text = path.read_text() if path.exists() else ""
        if kind == "css":
            if LEGACY_JARVIS_CSS in text:
                errors.append(f"Legacy Jarvis CSS still overrides the theme: {path}")
            continue
        values = GTK2_VALUES if kind == "gtk2" else XSETTINGS_VALUES
        separator = r"\s*=\s*" if kind == "gtk2" else r"\s+"
        for key, expected in values.items():
            matches = re.findall(
                r"^[ \t]*" + re.escape(key) + separator + r'"([^"\n]*)"', text, re.M
            )
            if not matches or not all(
                equivalent(value, expected, key) for value in matches
            ):
                errors.append(f"{path}: {key} does not select {expected}")
        if kind == "gtk2":
            includes = THEME_INCLUDE.findall(text)
            if (
                not includes
                or str(DATA / "themes/StarkOS/gtk-2.0/gtkrc") not in includes[-1]
            ):
                errors.append(
                    f"Effective GTK2 RC does not include the installed theme: {path}"
                )
    for key, record in gsettings("read").items():
        if not equivalent(record["value"], GSETTINGS_VALUES[key], key):
            errors.append(
                f"GSettings {key}: {record['value']!r} differs from STARK selection"
            )
    if errors:
        raise RuntimeError("\n".join(errors))
    print(
        "GTK transport audit: effective GTK2 RC, XSettings, GSettings and legacy CSS match STARK OS"
    )
