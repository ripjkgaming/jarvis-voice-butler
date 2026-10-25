"""Keyboard shortcuts (Classic Mode). The board UI repeats this table; a test keeps them in sync."""

from __future__ import annotations

SHORTCUTS: list[tuple[str, str]] = [
    ("type e2e4 / Nf3 + Enter", "Move (command bar is always focused)"),
    ("Ctrl+Z / Ctrl+Y", "Undo / redo"),
    ("F", "Flip board"),
    ("H", "Hint (once, escalates)"),
    ("A", "Analyse position (once)"),
    ("E", "Explain last move (once)"),
    ("T", "Take over / take back"),
    ("Space", "Pause / resume (spectator, or after a Jarvis call)"),
    ("Esc", "STOP everything"),
    ("L", "Square labels on/off"),
    ("Ctrl+S / Ctrl+O", "Save / load"),
    ("Ctrl+N", "New game"),
    ("F11", "Fullscreen"),
    ("?", "This overlay"),
    ("Q", "Quit (with the ASCII outro)"),
]

NOTE = (
    "With the command bar focused use Shift+F / H / A / E / T / L, or press Tab / click the "
    "board to leave the bar and use the plain letters. Ctrl+Q quits from anywhere."
)
