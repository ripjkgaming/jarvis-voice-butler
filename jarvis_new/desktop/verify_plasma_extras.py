#!/usr/bin/env python3
"""Headlessly check additive Plasma SVG contracts and deterministic generation.

Requires system PyQt6 QtSvg for the actual Plasma rendering engine. Does not
launch a visible window or read/change any live desktop preferences.
"""
from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

from build import PARTS
from build_plasma_extras import HERE, generate


def required_elements():
    required = {
        "checkmarks": {"checkbox", "radiobutton"},
        "radiobutton": {"normal", "checked", "focus", "hover", "symbol", "shadow", "hint-size"},
        "switch": {"handle", "handle-hover", "handle-active", "handle-pressed", "handle-focus", "handle-shadow", "hint-bar-size"} | {f"{state}-{part}" for state in ("active", "inactive") for part in ("left", "center", "right")},
        "arrows": {d + "-arrow" for d in ("up", "down", "left", "right")},
        "line": {"horizontal-line", "vertical-line"},
        "calendar": {"event"},
        "busywidget": {"busywidget", "22-22-busywidget", "16-16-busywidget"},
        "actionbutton": {size + state for size in ("", "16-16-", "22-22-", "24-24-") for state in ("normal", "hover", "focus", "pressed")},
        "action-overlays": {f"{action}-{state}" for action in ("add", "remove", "open") for state in ("normal", "hover", "pressed")},
    }
    for name, prefixes in {"pager": ("normal", "hover", "active"), "toolbar": ("",), "bar_meter_vertical": ("bar-active", "bar-inactive"), "scrollwidget": ("border",), "glowbar": ("",)}.items():
        required[name] = {f"{prefix}-{part}" if prefix else part for prefix in prefixes for part in PARTS}
    return required


def main():
    app = QApplication([])
    expected = required_elements()
    checked_ids = 0
    with tempfile.TemporaryDirectory(prefix="stark-plasma-verify-") as directory:
        paths = generate(Path(directory))
        assert {p.stem for p in paths} == set(expected), "Missing generated asset"
        for path in paths:
            installed_source = HERE / "plasma/desktoptheme/stark-os/widgets" / path.name
            assert path.read_bytes() == installed_source.read_bytes(), f"Stale asset: {installed_source}"
            document = ET.parse(path).getroot()
            ids = [element.get("id") for element in document.iter() if element.get("id")]
            assert len(ids) == len(set(ids)), f"Duplicate element ID: {path.name}"
            assert expected[path.stem] <= set(ids), f"Incomplete Plasma SVG contract: {path.name}"
            renderer = QSvgRenderer(str(path))
            assert renderer.isValid(), f"QtSvg rejected: {path.name}"
            for name in ids:
                assert renderer.elementExists(name), (path.name, name)
                bounds = renderer.boundsOnElement(name)
                assert bounds.width() > 0 and bounds.height() > 0, (path.name, name, bounds)
                # Render individual native elements without display or session bus.
                image = QImage(48, 48, QImage.Format.Format_ARGB32)
                image.fill(0)
                painter = QPainter(image)
                renderer.render(painter, name, QRectF(0, 0, 48, 48))
                painter.end()
                assert not image.isNull(), (path.name, name)
            checked_ids += len(ids)
    print(f"PASS: {len(paths)} SVG assets; {checked_ids} IDs/bounds/renderings; required Plasma contracts; byte-identical regeneration")
    app.quit()


if __name__ == "__main__":
    main()
