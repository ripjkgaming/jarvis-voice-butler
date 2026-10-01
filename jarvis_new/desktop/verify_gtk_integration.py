#!/usr/bin/env python3
"""Exercise GTK transport selection/rollback in a private keyfile/XDG sandbox."""

import os
from pathlib import Path
import tempfile

import gtk_integration as gtk


def main():
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        os.environ.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="stark-gtk-rollback-") as directory:
        root = Path(directory)
        config, data = root / "config", root / "data"
        config.mkdir()
        data.mkdir()
        os.environ["XDG_CONFIG_HOME"] = str(config)
        os.environ["XDG_DATA_HOME"] = str(data)
        os.environ["XDG_CACHE_HOME"] = str(root / "cache")
        os.environ["GSETTINGS_BACKEND"] = "keyfile"
        os.environ["GTK2_RC_FILES"] = str(root / ".gtkrc-2.0-kde4")
        gtk.configure_paths(root, config, data)
        assert not gtk.live_session(), "Must never call the real KDE daemon"
        alternate = root / ".gtkrc-2.0-kde4"
        original = 'gtk-theme-name="Adwaita"\ninclude "/usr/share/themes/Adwaita/gtk-2.0/gtkrc"\ngtk-enable-animations=0\n'
        alternate.write_text(original)
        xsettings = config / "xsettingsd/xsettingsd.conf"
        xsettings.parent.mkdir()
        xsettings.write_text('Net/ThemeName "OldTheme"\nGdk/WindowScalingFactor 2\n')
        for version in ("3.0", "4.0"):
            path = config / f"gtk-{version}/gtk.css"
            path.parent.mkdir()
            path.write_text(
                '@import "colors.css";\n'
                + gtk.LEGACY_JARVIS_CSS
                + "\n/* unrelated user CSS */\n"
            )
        # One explicit user value and six defaults must return to their original
        # state, not be replaced by explicit copies of their default values.
        gtk.gsettings("apply", {"gtk-theme": "OldGtkTheme"})
        previous_gsettings = gtk.gsettings("read")
        snapshot = root / "snapshot"
        snapshot.mkdir()
        gtk.backup(snapshot)
        gtk.apply()
        gtk.verify()
        once = {path: path.read_text() for _, path in gtk.file_inventory()}
        gtk.apply()
        assert once == {path: path.read_text() for _, path in gtk.file_inventory()}, (
            "Apply must be idempotent"
        )
        assert "gtk-enable-animations=0" in alternate.read_text()
        assert "Gdk/WindowScalingFactor 2" in xsettings.read_text()
        css = config / "gtk-3.0/gtk.css"
        assert '@import "colors.css";' in css.read_text()
        assert "/* unrelated user CSS */" in css.read_text()
        alternate.write_text(alternate.read_text() + "gtk-double-click-time=333\n")
        xsettings.write_text(xsettings.read_text() + "Gtk/EnableAnimations 0\n")
        css.write_text(css.read_text() + "/* later user CSS */\n")
        gtk.restore(snapshot)
        assert gtk.gsettings("read") == previous_gsettings
        assert 'gtk-theme-name="Adwaita"' in alternate.read_text()
        assert (
            'include "/usr/share/themes/Adwaita/gtk-2.0/gtkrc"' in alternate.read_text()
        )
        assert "gtk-double-click-time=333" in alternate.read_text()
        assert 'Net/ThemeName "OldTheme"' in xsettings.read_text()
        assert "Gtk/EnableAnimations 0" in xsettings.read_text()
        assert gtk.LEGACY_JARVIS_CSS in css.read_text()
        assert "/* later user CSS */" in css.read_text()
        restored = {path: path.read_text() for _, path in gtk.file_inventory()}
        gtk.restore(snapshot)
        assert restored == {
            path: path.read_text() for _, path in gtk.file_inventory()
        }, "Restore must be idempotent"
        assert gtk.gsettings("read") == previous_gsettings
        # Unknown user edits inside similarly marked CSS are never removed.
        unknown = (
            "/* Jarvis theme begin */\nbutton { opacity: 0.8; }\n/* Jarvis theme end */"
        )
        assert gtk.transform("css", unknown) == unknown
        # Inherited environment from another home cannot mutate that home.
        os.environ["GTK2_RC_FILES"] = "/home/someone-else/.gtkrc-2.0"
        assert gtk.gtk2_path() is None
    print(
        "PASS: isolated GTK2/XSettings/CSS/GSettings apply and rollback, default-vs-user values, preserved preferences, repeated operations, and path boundary"
    )


if __name__ == "__main__":
    main()
