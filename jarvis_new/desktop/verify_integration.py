#!/usr/bin/env python3
"""Exercise config/asset rollback in a temporary XDG tree, never the live desktop."""

import os
import tempfile
from pathlib import Path

import integration


def main():
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        os.environ.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="stark-rollback-") as temp:
        root = Path(temp)
        integration.HOME = root
        integration.CFG = root / "config"
        integration.DATA = root / "data"
        integration.CFG.mkdir()
        integration.DATA.mkdir()
        os.environ["XDG_CONFIG_HOME"] = str(integration.CFG)
        os.environ["XDG_DATA_HOME"] = str(integration.DATA)
        os.environ["XDG_CACHE_HOME"] = str(root / "cache")
        os.environ["XDG_RUNTIME_DIR"] = str(root / "runtime")
        os.environ["GTK2_RC_FILES"] = str(root / ".gtkrc-2.0-kde4")
        Path(os.environ["XDG_RUNTIME_DIR"]).mkdir(mode=0o700)
        integration.write("kdeglobals", ["KDE"], "widgetStyle", "OriginalStyle")
        integration.write("kdeglobals", ["Icons"], "Theme", "OriginalIcons")
        integration.write("kscreenlockerrc", ["Daemon"], "Timeout", "12")
        integration.write(
            "kscreenlockerrc",
            ["Wallpaper", "org.kde.image", "DP-1"],
            "Image",
            "file:///original.png",
        )
        asset = integration.DATA / "plasma/desktoptheme/stark-os/original.svg"
        asset.parent.mkdir(parents=True)
        asset.write_text("original asset bytes")
        launcher = integration.DATA / "applications/jarvis.desktop"
        launcher.parent.mkdir()
        launcher.write_text(
            "[Desktop Entry]\nIcon=audio-input-microphone\nExec=unchanged-command\n"
        )
        snapshot = root / "snapshot"
        snapshot.mkdir(mode=0o700)
        (root / ".gtkrc-2.0").write_text("gtk-modules=appmenu-gtk-module")
        (root / ".gtkrc-2.0-kde4").write_text('gtk-theme-name="Adwaita"\n')
        integration.backup(snapshot)
        integration.apply()
        integration.verify()
        assert integration.read("kscreenlockerrc", ["Daemon"], "Timeout") == "12"
        assert integration.read(str(launcher), ["Desktop Entry"], "Icon") == "jarvis"
        assert (
            integration.read(str(launcher), ["Desktop Entry"], "Exec")
            == "unchanged-command"
        )
        # A later unrelated preference must survive restoring the owned keys.
        integration.write("kdeglobals", ["General"], "UnrelatedPreference", "new-value")
        asset.write_text("replacement")
        integration.restore(snapshot)
        assert asset.read_text() == "original asset bytes"
        icon_theme = integration.DATA / "icons/stark-os"
        icon_theme.mkdir(parents=True)
        (icon_theme / "index.theme").write_text(
            "[Icon Theme]\nName=Previous artwork\nDirectories=\n"
        )
        (icon_theme / "original.svg").write_text("original icon bytes")
        config_before = (integration.CFG / "kdeglobals").read_bytes()
        icon_snapshot = root / "icon-snapshot"
        integration.update_icons(icon_snapshot)
        assert (icon_theme / "scalable/apps/jarvis.svg").is_file()
        assert (integration.CFG / "kdeglobals").read_bytes() == config_before
        integration.restore_icons(icon_snapshot)
        assert (icon_theme / "original.svg").read_text() == "original icon bytes"
        integration.remove(icon_theme)
        # On desktops without an alternate GTK2 RC, the fallback is active.
        os.environ["GTK2_RC_FILES"] = str(root / ".gtkrc-2.0")
        fallback_snapshot = root / "fallback-snapshot"
        fallback_snapshot.mkdir()
        integration.backup(fallback_snapshot)
        integration.apply()
        assert integration.GTK2_BLOCK.search((root / ".gtkrc-2.0").read_text())
        integration.restore(fallback_snapshot)
        assert (root / ".gtkrc-2.0").read_text() == "gtk-modules=appmenu-gtk-module"
        assert integration.read("kdeglobals", ["KDE"], "widgetStyle") == "OriginalStyle"
        assert integration.read("kdeglobals", ["Icons"], "Theme") == "OriginalIcons"
        assert (
            integration.read("gtk-3.0/settings.ini", ["Settings"], "gtk-theme-name")
            is None
        )
        assert (
            integration.read("kdeglobals", ["General"], "UnrelatedPreference")
            == "new-value"
        )
        assert (
            integration.read(
                "kscreenlockerrc", ["Wallpaper", "org.kde.image", "DP-1"], "Image"
            )
            == "file:///original.png"
        )
        assert integration.read("kscreenlockerrc", ["Daemon"], "Timeout") == "12"
        assert not (integration.DATA / "icons/stark-os").exists()
        assert (root / ".gtkrc-2.0").read_text() == "gtk-modules=appmenu-gtk-module"
        assert (
            integration.read(str(launcher), ["Desktop Entry"], "Icon")
            == "audio-input-microphone"
        )
        # Restoration can be safely repeated.
        integration.restore(snapshot)
        assert asset.read_text() == "original asset bytes"
    print(
        "PASS: sandbox install, owned-key rollback, prior assets, absent assets, repeat restore, and native security preservation"
    )


if __name__ == "__main__":
    main()
