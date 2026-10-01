#!/usr/bin/env python3
"""Parse GTK themes, SVGs and Xcursors with native libraries, without a display.

Run with /usr/bin/python3 (system PyGObject, GTK3/4 and libXcursor required).
This verifier reads assets only; it never opens a window, connects to the user's
session bus, changes preferences or regenerates assets. --assets accepts an
isolated output directory from build_app_extras.py.
"""

from __future__ import annotations

import argparse
import configparser
import ctypes
import ctypes.util
import os
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
SIZES = (24, 32, 48, 64)


def isolate() -> None:
    for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        os.environ.pop(name, None)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"


def gtk_child(version: str, assets: Path) -> None:
    # GTK major versions cannot coexist in one process. Never initialize GTK or
    # request a display: the CSS parser itself does not need either.
    import gi

    gi.require_version("Gtk", version)
    from gi.repository import Gtk

    for name in ("gtk.css", "gtk-dark.css"):
        path = assets / "themes/StarkOS" / f"gtk-{version}" / name
        errors = []
        provider = Gtk.CssProvider()
        provider.connect(
            "parsing-error",
            lambda _provider, _section, error, errors=errors: errors.append(str(error)),
        )
        provider.load_from_path(str(path))
        if errors:
            raise RuntimeError(f"{path}: " + "; ".join(errors))
    print(
        f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}: both CSS entry points accepted"
    )


def check_gtk(assets: Path) -> None:
    for version in ("3.0", "4.0"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--assets",
                str(assets),
                "--gtk-parser",
                version,
            ],
            capture_output=True,
            text=True,
            env=os.environ,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        # Native GTK base CSS may request the default icon theme while parsing;
        # report the limitation without mistaking it for a CSS parsing error.
        diagnostics = [line for line in result.stderr.splitlines() if line.strip()]
        expected = (
            "gtk_icon_theme_get_for_screen",
            "gtk_icon_theme_has_icon",
            "gtk_icon_theme_get_for_display",
            "gtk_icon_theme_lookup_icon",
            "gdk_display_get_default_screen",
        )
        unexpected = [
            line for line in diagnostics if not any(item in line for item in expected)
        ]
        if unexpected:
            raise RuntimeError(
                "Unexpected native GTK diagnostics:\n" + "\n".join(unexpected)
            )
        print(result.stdout.strip())
        if diagnostics:
            print(
                f"  {len(diagnostics)} expected no-display icon-theme diagnostics; rendering is not exercised"
            )


class XcursorImage(ctypes.Structure):
    # Public layout from X11/Xcursor/Xcursor.h; these are 32-bit unsigned types.
    _fields_ = [
        (name, ctypes.c_uint32)
        for name in ("version", "size", "width", "height", "xhot", "yhot", "delay")
    ]
    _fields_ += [("pixels", ctypes.POINTER(ctypes.c_uint32))]


class XcursorImages(ctypes.Structure):
    _fields_ = [
        ("nimage", ctypes.c_int),
        ("images", ctypes.POINTER(ctypes.POINTER(XcursorImage))),
        ("name", ctypes.c_char_p),
    ]


def check_cursors(assets: Path) -> None:
    library = ctypes.util.find_library("Xcursor")
    if not library:
        raise RuntimeError("libXcursor is required for native decoder verification")
    lib = ctypes.CDLL(library)
    lib.XcursorFilenameLoadImages.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.XcursorFilenameLoadImages.restype = ctypes.POINTER(XcursorImages)
    lib.XcursorImagesDestroy.argtypes = [ctypes.POINTER(XcursorImages)]
    lib.XcursorImagesDestroy.restype = None
    paths = sorted((assets / "icons/stark-os-cursors/cursors").iterdir())
    assert paths, "No cursor files"
    count = 0
    for path in paths:
        for size in SIZES:
            images = lib.XcursorFilenameLoadImages(os.fsencode(path), size)
            assert images, f"libXcursor rejected {path.name} at {size}px"
            try:
                assert images.contents.nimage == 1, (
                    f"Expected one static frame: {path.name}"
                )
                frame = images.contents.images[0].contents
                assert (frame.size, frame.width, frame.height) == (size, size, size), (
                    path.name,
                    size,
                )
                assert 0 <= frame.xhot < size and 0 <= frame.yhot < size, (
                    path.name,
                    "hotspot",
                )
                assert frame.delay == 0, (path.name, "animated cursor")
                pixels = frame.pixels[: size * size]
                assert any(pixel >> 24 for pixel in pixels), (path.name, "empty image")
                assert any(not pixel >> 24 for pixel in pixels), (
                    path.name,
                    "opaque background",
                )
                assert all(
                    max((p >> 16) & 255, (p >> 8) & 255, p & 255) <= p >> 24
                    for p in pixels
                ), (path.name, "non-premultiplied ARGB")
                count += 1
            finally:
                lib.XcursorImagesDestroy(images)
    print(
        f"libXcursor: {len(paths)} cursor files; {count} native decodes at {', '.join(map(str, SIZES))}px; hotspots and premultiplied pixels valid"
    )


def check_icons(assets: Path) -> None:
    paths = sorted((assets / "icons/stark-os").rglob("*.svg"))
    assert paths, "No SVG icons"
    for path in paths:
        root = ET.parse(path).getroot()
        assert root.tag == "{http://www.w3.org/2000/svg}svg", (path, "not SVG")
        identifiers = [node.attrib["id"] for node in root.iter() if "id" in node.attrib]
        assert len(identifiers) == len(set(identifiers)), (path, "duplicate SVG IDs")
        assert root.get("viewBox"), (path, "no scalable viewBox")
    print(f"SVG XML: {len(paths)} icons valid with unique IDs and scalable viewBoxes")


def check_fonts(installed: bool = False) -> None:
    from PyQt6.QtGui import QFont

    def decode(spec: str, family: str, size: int, weight: int, label: str) -> None:
        font = QFont()
        assert font.fromString(spec), (label, "invalid QFont")
        actual = (font.family(), font.pointSize(), font.weight())
        assert actual == (family, size, weight), (
            f"{label}: decoded {actual}, expected {(family, size, weight)}"
        )

    expected = {
        "font": ("Rajdhani", 11, 500),
        "menuFont": ("Rajdhani", 11, 500),
        "toolBarFont": ("Rajdhani", 11, 500),
        "smallestReadableFont": ("Rajdhani", 9, 500),
        "fixed": ("Share Tech Mono", 10, 400),
        "activeFont": ("Rajdhani", 11, 600),
    }
    seen = set()
    for line in (HERE / "install.sh").read_text().splitlines():
        if not line.startswith("kw --file kdeglobals "):
            continue
        words = shlex.split(line)
        key = words[words.index("--key") + 1]
        if key in expected:
            decode(words[-1], *expected[key], f"installer {key}")
            seen.add(key)
    assert seen == set(expected), "Missing installer font assignments"
    profile = configparser.ConfigParser()
    profile.read(HERE / "konsole/StarkOS.profile")
    decode(
        profile["Appearance"]["Font"],
        "Share Tech Mono",
        10,
        400,
        "source Konsole profile",
    )
    if installed:
        import integration

        for key, values in expected.items():
            group = "WM" if key == "activeFont" else "General"
            spec = integration.read("kdeglobals", [group], key)
            assert spec is not None, f"Missing installed font: {key}"
            decode(spec, *values, f"installed {key}")
        profile.read(integration.DATA / "konsole/StarkOS.profile")
        decode(
            profile["Appearance"]["Font"],
            "Share Tech Mono",
            10,
            400,
            "installed Konsole profile",
        )
    print(
        "QFont: native family, size and weight decoding passes for KDE/ Konsole"
        + (" source and installed preferences" if installed else " source preferences")
    )


def check_installed() -> None:
    """Read configuration/asset bytes only; no calls to apply or notification APIs."""
    import gtk_integration
    import integration

    integration.verify()
    errors = []
    gtk2 = integration.HOME / ".gtkrc-2.0"
    block = integration.GTK2_BLOCK.search(gtk2.read_text() if gtk2.exists() else "")
    required = (
        str(integration.DATA / "themes/StarkOS/gtk-2.0/gtkrc"),
        'gtk-theme-name="StarkOS"',
        'gtk-icon-theme-name="stark-os"',
        'gtk-cursor-theme-name="stark-os-cursors"',
        'gtk-font-name="Rajdhani Medium 11"',
    )
    if gtk_integration.gtk2_path() is None and (
        not block or any(item not in block.group() for item in required)
    ):
        errors.append(
            "GTK2 managed include/settings block is absent or incomplete in ~/.gtkrc-2.0"
        )
    for name in ("StarkOS.profile", "StarkOS.colorscheme"):
        source = HERE / "konsole" / name
        target = integration.DATA / "konsole" / name
        if not target.is_file() or source.read_bytes() != target.read_bytes():
            errors.append(f"Installed Konsole asset differs: {name}")
    if (
        integration.read("konsolerc", ["Desktop Entry"], "DefaultProfile")
        != "StarkOS.profile"
    ):
        errors.append("Konsole default is not StarkOS.profile")
    icon_roots = [integration.DATA / "icons"] + [
        Path(path) / "icons"
        for path in os.environ.get(
            "XDG_DATA_DIRS", "/usr/local/share:/usr/share"
        ).split(":")
    ]
    for name in ("breeze-dark", "breeze", "hicolor", "breeze_cursors"):
        if not any((root / name / "index.theme").is_file() for root in icon_roots):
            errors.append(f"Missing inherited icon/cursor theme: {name}")
    if errors:
        raise RuntimeError("Installed coverage discrepancies:\n" + "\n".join(errors))
    check_fonts(installed=True)
    print(
        "Installed audit: managed GTK2 selection, GTK3/4, settings/assets, Konsole and inherited themes match"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, default=HERE / "app-extras")
    parser.add_argument(
        "--installed",
        action="store_true",
        help="Also read and check the installed application selection/assets",
    )
    parser.add_argument("--gtk-parser", choices=("3.0", "4.0"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    isolate()
    assets = args.assets.resolve()
    if args.gtk_parser:
        gtk_child(args.gtk_parser, assets)
        return
    check_gtk(assets)
    check_icons(assets)
    check_cursors(assets)
    check_fonts()
    if args.installed:
        check_installed()
    print(
        "PASS: native application assets checked without display, session bus or live mutations"
    )


if __name__ == "__main__":
    main()
