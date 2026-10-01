#!/usr/bin/env python3
"""Apply expanded STARK styling; snapshot only owned settings and assets.

Called by install.sh/revert.sh. No GUI, authentication, or session restarts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
CFG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
DATA = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
ASSETS = [
    "color-schemes/StarkOS.colors",
    "plasma/desktoptheme/stark-os",
    "aurorae/themes/stark-os",
    "plasma/look-and-feel/org.jarvis.starkos",
    "wallpapers/StarkOS",
    "konsole/StarkOS.profile",
    "konsole/StarkOS.colorscheme",
    "fonts/stark-os",
    "themes/StarkOS",
    "icons/stark-os",
    "icons/stark-os-cursors",
]
GTK2_BLOCK = re.compile(r"\n?# BEGIN STARK OS\n.*?# END STARK OS\n?", re.S)


def update_gtk2(block):
    path = HOME / ".gtkrc-2.0"
    content = path.read_text() if path.exists() else ""
    clean = GTK2_BLOCK.sub("", content)
    path.write_text(clean + block)


def settings():
    entries = [
        ("kdeglobals", ["KDE"], "widgetStyle", "Breeze"),
        ("kdeglobals", ["Icons"], "Theme", "stark-os"),
        ("kcminputrc", ["Mouse"], "cursorTheme", "stark-os-cursors"),
        ("kwinrc", ["TabBox"], "LayoutName", "compact"),
        ("kdeglobals", ["General"], "ColorScheme", "StarkOS"),
    ]
    # Correct existing user-local launchers without changing their commands.
    for filename, icon in (
        ("jarvis.desktop", "jarvis"),
        ("jarvis-projects.desktop", "jarvis-projects"),
    ):
        launcher = DATA / "applications" / filename
        if launcher.is_file():
            entries.append((str(launcher), ["Desktop Entry"], "Icon", icon))
    for version in ("3.0", "4.0"):
        for key, value in {
            "gtk-theme-name": "StarkOS",
            "gtk-icon-theme-name": "stark-os",
            "gtk-font-name": "Rajdhani Medium 11",
            "gtk-cursor-theme-name": "stark-os-cursors",
            "gtk-application-prefer-dark-theme": "true",
        }.items():
            entries.append((f"gtk-{version}/settings.ini", ["Settings"], key, value))
    # Preserve lock timeout, password/PAM, fingerprint, and greeter implementation.
    wallpaper = (DATA / "wallpapers/StarkOS/stark-os-sector-01.png").as_uri()
    groups = [
        ["Greeter", "Wallpaper", "org.kde.image", "General"],
        ["Wallpaper", "org.kde.image", "General"],
    ]
    lock = CFG / "kscreenlockerrc"
    if lock.exists():
        for name in re.findall(
            r"^\[Wallpaper\]\[org\.kde\.image\]\[([^\]]+)\]$", lock.read_text(), re.M
        ):
            group = ["Wallpaper", "org.kde.image", name]
            if group not in groups:
                groups.append(group)
    for group in groups:
        entries.append(("kscreenlockerrc", group, "Image", wallpaper))
    entries.append(("kscreenlockerrc", groups[0], "PreviewImage", wallpaper))
    return entries


def config_args(file, groups, key):
    args = ["--file", str(CFG / file)]
    for group in groups:
        args.extend(["--group", group])
    return [*args, "--key", key]


def read(file, groups, key):
    sentinel = "__STARK_ABSENT_f917d0__"
    value = subprocess.check_output(
        ["kreadconfig6", *config_args(file, groups, key), "--default", sentinel],
        text=True,
    ).rstrip("\n")
    return None if value == sentinel else value


def write(file, groups, key, value):
    (CFG / file).parent.mkdir(parents=True, exist_ok=True)
    args = ["kwriteconfig6", "--notify", *config_args(file, groups, key)]
    subprocess.run(args + (["--delete"] if value is None else [value]), check=True)


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target, follow_symlinks=False)


def refresh_cursor(name):
    # This native CLI broadcasts the cursor change; it creates no application
    # window and never restarts the compositor. Skip in an isolated verifier.
    if (
        name
        and os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        and shutil.which("plasma-apply-cursortheme")
    ):
        subprocess.run(["plasma-apply-cursortheme", name], check=False)


def refresh_icons(rebuild_services=True):
    theme = DATA / "icons/stark-os"
    if theme.exists() and shutil.which("gtk-update-icon-cache"):
        subprocess.run(
            ["gtk-update-icon-cache", "-f", "-t", str(theme)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS") and shutil.which("dbus-send"):
        # Native KIconLoader theme-change signal, confirmed in KF6IconThemes.
        # It refreshes caches in clients without restarting Plasma or KWin.
        subprocess.run(
            [
                "dbus-send",
                "--session",
                "--type=signal",
                "/KIconLoader",
                "org.kde.KIconLoader.iconChanged",
                "int32:0",
            ],
            check=True,
        )
    if rebuild_services and shutil.which("kbuildsycoca6"):
        subprocess.run(
            ["kbuildsycoca6", "--noincremental"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def update_icons(root):
    """Apply an artwork revision without rerunning other desktop settings."""
    source = HERE / "app-extras/icons/stark-os"
    target = DATA / "icons/stark-os"
    if not (source / "index.theme").is_file():
        raise ValueError("Generated STARK icon theme is missing")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    existed = target.exists()
    if existed:
        copy(target, root / "icons/stark-os")
    (root / "icon-update.json").write_text(
        json.dumps({"version": 1, "existed": existed}) + "\n"
    )
    remove(target)
    copy(source, target)
    refresh_icons(rebuild_services=False)
    print(f"Icon artwork updated. Revert with desktop/revert.sh {root}")


def restore_icons(root):
    saved = json.loads((root / "icon-update.json").read_text())
    target = DATA / "icons/stark-os"
    remove(target)
    if saved["existed"]:
        copy(root / "icons/stark-os", target)
    refresh_icons(rebuild_services=False)


def backup(root):
    import gtk_integration

    gtk_integration.configure_paths(HOME, CFG, DATA)
    gtk_integration.backup(root)
    records = [
        {"file": f, "groups": g, "key": k, "value": read(f, g, k)}
        for f, g, k, _ in settings()
    ]
    existing = []
    for rel in ASSETS:
        path = DATA / rel
        if path.exists() or path.is_symlink():
            copy(path, root / "assets" / rel)
            existing.append(rel)
    # Keep exact GTK files for manual recovery, restore keys selectively.
    for rel in ("gtk-3.0/settings.ini", "gtk-4.0/settings.ini", "kscreenlockerrc"):
        if (CFG / rel).exists():
            copy(CFG / rel, root / "extra-config" / rel)
    for name in ("jarvis.desktop", "jarvis-projects.desktop"):
        launcher = DATA / "applications" / name
        if launcher.is_file():
            copy(launcher, root / "extra-config/applications" / name)
    gtk2 = HOME / ".gtkrc-2.0"
    content = gtk2.read_text() if gtk2.exists() else ""
    match = GTK2_BLOCK.search(content)
    if gtk2.exists():
        copy(gtk2, root / "extra-config/gtkrc-2.0")
    (root / "integration.json").write_text(
        json.dumps(
            {
                "version": 1,
                "settings": records,
                "assets": ASSETS,
                "existing_assets": existing,
                "gtk2_existed": gtk2.exists(),
                "gtk2_block": match.group() if match else "",
            },
            indent=2,
        )
        + "\n"
    )


def apply():
    for rel in ("themes/StarkOS", "icons/stark-os", "icons/stark-os-cursors"):
        remove(DATA / rel)
        copy(HERE / "app-extras" / rel, DATA / rel)
    refresh_cursor("stark-os-cursors")
    for file, groups, key, value in settings():
        write(file, groups, key, value)
    import gtk_integration

    gtk_integration.configure_paths(HOME, CFG, DATA)
    gtk_integration.apply()
    # KDE can own a different active GTK2 file via GTK2_RC_FILES. Do not
    # require or keep rewriting an unused fallback that KDE may regenerate.
    if gtk_integration.gtk2_path() is None:
        gtkrc = json.dumps(str(DATA / "themes/StarkOS/gtk-2.0/gtkrc"))
        update_gtk2(
            f"\n# BEGIN STARK OS\ninclude {gtkrc}\n"
            'gtk-theme-name="StarkOS"\ngtk-icon-theme-name="stark-os"\n'
            'gtk-cursor-theme-name="stark-os-cursors"\ngtk-font-name="Rajdhani Medium 11"\n'
            "# END STARK OS\n"
        )
    refresh_icons()


def restore(root):
    manifest = root / "integration.json"
    if not manifest.exists():
        return
    saved = json.loads(manifest.read_text())
    for rel in saved["assets"]:
        if rel not in ASSETS:
            raise ValueError(f"Unexpected asset in backup: {rel}")
        remove(DATA / rel)
        if rel in saved["existing_assets"]:
            copy(root / "assets" / rel, DATA / rel)
    for record in saved["settings"]:
        if record["file"] == "kcminputrc" and record["key"] == "cursorTheme":
            refresh_cursor(record["value"])
        write(record["file"], record["groups"], record["key"], record["value"])
    update_gtk2(saved.get("gtk2_block", ""))
    gtk2 = HOME / ".gtkrc-2.0"
    if not saved.get("gtk2_existed", True) and not gtk2.read_text():
        gtk2.unlink()
    import gtk_integration

    gtk_integration.configure_paths(HOME, CFG, DATA)
    gtk_integration.restore(root)
    refresh_icons()


def verify():
    errors = []
    for file, groups, key, value in settings():
        actual = read(file, groups, key)
        equivalent = actual == value
        if key == "gtk-font-name" and actual:
            # KDE serializes the same Pango family/weight with a comma.
            equivalent = actual.replace(", ", " ") == value.replace(", ", " ")
        if not equivalent:
            errors.append(
                f"{file} [{']['.join(groups)}] {key}: {actual!r} != {value!r}"
            )
    for rel in ("themes/StarkOS", "icons/stark-os", "icons/stark-os-cursors"):
        for source in (HERE / "app-extras" / rel).rglob("*"):
            if source.is_file():
                target = DATA / source.relative_to(HERE / "app-extras")
                if not target.is_file() or source.read_bytes() != target.read_bytes():
                    errors.append(f"Asset mismatch: {target}")
    if errors:
        raise SystemExit("\n".join(errors))
    import gtk_integration

    gtk_integration.configure_paths(HOME, CFG, DATA)
    gtk_integration.verify()
    print("Expanded desktop settings and application assets match STARK OS.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "backup",
            "apply",
            "restore",
            "verify",
            "update-icons",
            "restore-icons",
        ),
    )
    parser.add_argument("backup", type=Path, nargs="?")
    args = parser.parse_args()
    action = args.action.replace("-", "_")
    if args.action in ("backup", "restore", "update-icons", "restore-icons"):
        if args.backup is None:
            parser.error("backup directory required")
        globals()[action](args.backup)
    else:
        globals()[action]()
