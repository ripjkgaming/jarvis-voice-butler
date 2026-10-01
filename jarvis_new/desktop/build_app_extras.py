#!/usr/bin/env python3
"""Generate additive application assets; never change live settings.

Uses the canonical desktop/build.py palette. Standard library only, including
the static multi-size Xcursor encoder. --output supports isolated regeneration.
"""

from __future__ import annotations

import argparse
import math
import struct
from pathlib import Path

import build as tokens


def write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def gtk(root: Path) -> None:
    colors = {name.lower(): tokens.hexc(getattr(tokens, name)) for name in (
        "INK", "DEEP", "RAISE", "CYAN", "CYAN_HOT", "CYAN_DIM", "SELECT",
        "TEXT", "TEXT_DIM", "TEXT_FAINT", "AMBER", "RED", "GREEN",
        "LINE_SOLID", "LINE_STRONG_SOLID")}
    defines = "\n".join(f"@define-color stark_{k} {v};" for k, v in colors.items())
    # Import toolkit defaults first: retain working check/radio arrows, spinners,
    # accessibility glyphs and geometry, then override colors and instrument edges.
    css = """
@define-color theme_bg_color @stark_deep;
@define-color theme_fg_color @stark_text;
@define-color theme_base_color @stark_ink;
@define-color theme_text_color @stark_text;
@define-color theme_selected_bg_color @stark_select;
@define-color theme_selected_fg_color @stark_cyan_hot;
@define-color insensitive_fg_color @stark_text_dim;
@define-color borders @stark_line_solid;
@define-color warning_color @stark_amber;
@define-color error_color @stark_red;
@define-color success_color @stark_green;
* { text-shadow: none; -gtk-icon-shadow: none; }
window, dialog, .background { background-color: @stark_deep; color: @stark_text; }
headerbar, menubar, toolbar, .toolbar, .titlebar {
  background-image: none; background-color: @stark_deep; color: @stark_cyan_hot;
  border-color: @stark_line_solid; box-shadow: none;
}
headerbar .title { font-weight: 600; }
button, combobox button, spinbutton button {
  background-image: none; background-color: @stark_raise; color: @stark_text;
  border: 1px solid @stark_line_solid; border-radius: 2px; box-shadow: none;
}
button:hover { background-color: @stark_select; border-color: @stark_line_strong_solid; }
button:active, button:checked, button.suggested-action {
  background-color: @stark_select; color: @stark_cyan_hot; border-color: @stark_cyan_dim;
}
button.destructive-action { color: @stark_red; }
button.destructive-action:hover { border-color: @stark_red; }
entry, spinbutton, textview, textview text, treeview, .view, list, listview, gridview {
  background-color: @stark_ink; color: @stark_text;
}
entry, spinbutton { border: 1px solid @stark_line_solid; border-radius: 2px; box-shadow: none; }
*:focus { outline: 1px solid @stark_cyan; outline-offset: -2px; }
entry:focus { border-color: @stark_cyan; }
selection, *:selected, row:selected, treeview:selected {
  background-color: @stark_select; color: @stark_cyan_hot;
}
row:hover { background-color: @stark_raise; }
menu, popover, popover > contents, .menu {
  background-color: @stark_deep; color: @stark_text;
  border: 1px solid @stark_line_strong_solid; border-radius: 2px;
}
menuitem:hover, modelbutton:hover { background-color: @stark_select; color: @stark_cyan_hot; }
tooltip, tooltip.background { background-color: @stark_deep; color: @stark_text;
  border: 1px solid @stark_line_strong_solid; border-radius: 2px; }
tooltip label { color: @stark_text; }
notebook > header { background-color: @stark_deep; border-color: @stark_line_solid; }
notebook > header tab { color: @stark_text_dim; }
notebook > header tab:checked { color: @stark_cyan_hot; box-shadow: inset 0 -2px @stark_cyan; }
separator { background-color: @stark_line_solid; min-width: 1px; min-height: 1px; }
scrollbar, scrollbar trough { background-color: @stark_ink; }
scrollbar slider { background-color: @stark_cyan_dim; border: none; min-width: 6px; min-height: 6px; }
scrollbar slider:hover { background-color: @stark_cyan; }
scale trough, progressbar trough, levelbar trough { background-color: @stark_ink; border-color: @stark_line_solid; }
scale highlight, progressbar progress, levelbar block.filled { background-color: @stark_cyan; border-color: @stark_cyan; }
scale slider { background-color: @stark_cyan_hot; background-image: none; border-color: @stark_cyan; }
check, radio, switch { background-color: @stark_raise; border-color: @stark_cyan_dim; }
check:checked, radio:checked, switch:checked { background-color: @stark_select; color: @stark_cyan_hot; }
switch slider { background-image: none; background-color: @stark_cyan_hot; }
link, button.link, label link { color: @stark_cyan; }
.dim-label, *:disabled { color: @stark_text_dim; }
.error { color: @stark_red; }
.warning { color: @stark_amber; }
.success { color: @stark_green; }
"""
    for version, base in (("3.0", "Adwaita/gtk-contained-dark.css"), ("4.0", "Default/gtk-dark.css")):
        content = f'/* STARK OS; canonical tokens, native GTK widget semantics. */\n@import url("resource:///org/gtk/libgtk/theme/{base}");\n' + defines + css
        write(root, f"themes/StarkOS/gtk-{version}/gtk.css", content)
        write(root, f"themes/StarkOS/gtk-{version}/gtk-dark.css", '@import url("gtk.css");\n')
    # Engine-free GTK2 fallback: no dependency on abandoned third-party engines.
    gtk2 = ['# STARK OS GTK2: native engine, canonical palette.', 'gtk-font-name = "Rajdhani Medium 11"', 'style "stark-default" {']
    for state in ("NORMAL", "ACTIVE", "PRELIGHT", "SELECTED", "INSENSITIVE"):
        bg = "select" if state == "SELECTED" else "raise" if state == "PRELIGHT" else "deep"
        fg = "text_dim" if state == "INSENSITIVE" else "cyan_hot" if state == "SELECTED" else "text"
        for key, value in (("bg", bg), ("fg", fg), ("base", "select" if state == "SELECTED" else "ink"), ("text", fg)):
            gtk2.append(f'  {key}[{state}] = "{colors[value]}"')
    gtk2 += ['  xthickness = 1', '  ythickness = 1', '}', 'class "*" style "stark-default"']
    write(root, "themes/StarkOS/gtk-2.0/gtkrc", "\n".join(gtk2) + "\n")
    write(root, "themes/StarkOS/index.theme", "[Desktop Entry]\nType=X-GNOME-Metatheme\nName=STARK OS\nComment=Jarvis cyan instrument glass\n\n[X-GNOME-Metatheme]\nGtkTheme=StarkOS\nIconTheme=stark-os\nCursorTheme=stark-os-cursors\n")


def icons(root: Path) -> None:
    # Detailed masters and optical small sizes share one dedicated generator.
    from build_icon_extras import icons as detailed_icons

    detailed_icons(root)


def in_polygon(x: float, y: float, points: list[tuple[float, float]]) -> bool:
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


def cursor_pixel(kind: str, x: float, y: float) -> tuple[int, int, int, int]:
    # Draw in a 32px coordinate space. The outline remains dark against light UI.
    arrow = [(3, 2), (3, 26), (9, 20), (14, 30), (19, 27), (14, 18), (23, 18)]
    inner = [(4.6, 5.6), (4.6, 22), (9.5, 17.6), (14.7, 27.7), (16.7, 26.6), (11.5, 16.4), (19, 16.4)]
    if kind in ("left_ptr", "progress"):
        if kind == "progress" and 5 < math.hypot(x - 24, y - 8) < 7:
            return (*tokens.CYAN, 255)
        if in_polygon(x, y, inner):
            return (*tokens.CYAN_HOT, 255)
        if in_polygon(x, y, arrow):
            return (*tokens.INK, 255)
    elif kind == "text":
        if (13 <= x <= 19 and 4 <= y <= 28) or (9 <= x <= 23 and (3 <= y <= 7 or 25 <= y <= 29)):
            if (15 <= x <= 17 and 5 <= y <= 27) or (11 <= x <= 21 and (4.5 <= y <= 5.5 or 26.5 <= y <= 27.5)):
                return (*tokens.CYAN_HOT, 255)
            return (*tokens.INK, 255)
    elif kind == "crosshair":
        dx, dy = abs(x - 16), abs(y - 16)
        if (dx <= 2 and 4 <= dy <= 13) or (dy <= 2 and 4 <= dx <= 13):
            return (*(tokens.CYAN if min(dx, dy) <= .75 else tokens.INK), 255)
    elif kind == "wait":
        radius = math.hypot(x - 16, y - 16)
        if 8 <= radius <= 13:
            return (*(tokens.CYAN if 9.5 <= radius <= 11.5 else tokens.INK), 255)
        if radius < 3:
            return (*tokens.CYAN_HOT, 255)
    return (0, 0, 0, 0)


def xcursor(kind: str) -> bytes:
    chunks = []
    sizes = (24, 32, 48, 64)
    for size in sizes:
        pixels = []
        for py in range(size):
            for px in range(size):
                # 4x supersampling, premultiplied ARGB mandated by Xcursor.
                samples = [cursor_pixel(kind, (px + (sx + .5) / 4) * 32 / size,
                                        (py + (sy + .5) / 4) * 32 / size)
                           for sy in range(4) for sx in range(4)]
                r, g, b, a = [round(sum(c[i] for c in samples) / 16) for i in range(4)]
                pixels.append((a << 24) | (r << 16) | (g << 8) | b)
        hotspot = (3, 2) if kind in ("left_ptr", "progress") else (16, 16)
        chunk = struct.pack("<9I", 36, 0xFFFD0002, size, 1, size, size,
                            round(hotspot[0] * size / 32), round(hotspot[1] * size / 32), 0)
        chunks.append(chunk + struct.pack(f"<{len(pixels)}I", *pixels))
    position = 16 + 12 * len(sizes)
    toc = b""
    for size, chunk in zip(sizes, chunks):
        toc += struct.pack("<3I", 0xFFFD0002, size, position)
        position += len(chunk)
    return struct.pack("<4I", 0x72756358, 16, 0x10000, len(sizes)) + toc + b"".join(chunks)


def cursors(root: Path) -> None:
    base = root / "icons/stark-os-cursors"
    write(base, "index.theme", "[Icon Theme]\nName=STARK OS Cursors\nComment=High contrast static instrument pointers\nInherits=breeze_cursors\n")
    (base / "cursors").mkdir(parents=True, exist_ok=True)
    names = {"left_ptr": ["default", "arrow", "top_left_arrow"], "text": ["xterm", "ibeam"], "crosshair": ["cross"], "wait": ["watch"], "progress": ["left_ptr_watch", "00000000000000020006000e7e9ffc3f"]}
    for kind, aliases in names.items():
        data = xcursor(kind)
        for name in [kind, *aliases]:
            (base / "cursors" / name).write_bytes(data)


def terminal(root: Path) -> None:
    # Opt-in files only: never rewrite shell startup, prompt or terminal config.
    palette = [tokens.DEEP, tokens.RED, tokens.GREEN, tokens.AMBER, tokens.CYAN_DIM,
               (180, 140, 255), tokens.CYAN, tokens.TEXT,
               (16, 48, 68), (255, 122, 134), (160, 255, 205), (255, 205, 130),
               (90, 180, 220), (205, 180, 255), tokens.CYAN_HOT, (255, 255, 255)]
    content = "# Optional Kitty include; STARK OS color profile\nfont_family Share Tech Mono\nfont_size 10.0\n"
    for name, color in (("background", tokens.INK), ("foreground", tokens.TEXT), ("cursor", tokens.CYAN), ("selection_background", tokens.SELECT), ("selection_foreground", tokens.CYAN_HOT)):
        content += f"{name} {tokens.hexc(color)}\n"
    content += "\n".join(f"color{i} {tokens.hexc(color)}" for i, color in enumerate(palette)) + "\n"
    write(root, "terminal/kitty-stark-os.conf", content)
    # Konsole's canonical profile lives in desktop/konsole/StarkOS.profile and
    # is generated by build.py. Do not create a second competing default.


def main(output: Path | None = None) -> None:
    output = output or Path(__file__).resolve().parent / "app-extras"
    gtk(output)
    icons(output)
    cursors(output)
    terminal(output)
    print(f"STARK OS application assets generated in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    main(parser.parse_args().output)
