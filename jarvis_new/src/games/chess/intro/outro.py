"""Outro (king tips over), engine warm-up knight spinner, and the --help header."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable
from typing import TypeVar

from rich.console import Console
from rich.live import Live
from rich.text import Text

from games.chess.intro import scenes
from games.chess.intro.player import detect_colour

T = TypeVar("T")

# Hand-drawn king, upright then tipping over.
_KING = [
    [
        "     +     ",
        "    _|_    ",
        "   ( ♚ )   ",
        "    | |    ",
        "   /___\\   ",
        "  [_____]  ",
    ],
    [
        "           ",
        "      +    ",
        "    _/_    ",
        "   ( ♚ )   ",
        "   /| |    ",
        "  [_____]  ",
    ],
    [
        "           ",
        "           ",
        "           ",
        "   __+__   ",
        "  (_ ♚ _)>>",
        "  [_____]  ",
    ],
    [
        "           ",
        "           ",
        "           ",
        "           ",
        "  ___+___  ",
        " [__(♚)__] ",
    ],
]


def _ascii(lines: list[str]) -> list[str]:
    return [ln.replace("♚", "K").replace("♞", "N") for ln in lines]


def king_frames(letters: bool = False) -> list[Text]:
    out = []
    for fr in _KING:
        lines = _ascii(fr) if letters else fr
        out.append(Text("\n".join(lines), style=scenes.AMBER))
    return out


def outro_lines(result: str, moves: int) -> list[str]:
    res = {"1-0": "White wins", "0-1": "Black wins", "1/2-1/2": "A draw"}.get(
        result, "Game left unfinished"
    )
    return ["Good game, sir.", f"{res} after {moves} move{'s' if moves != 1 else ''}."]


def run_outro(
    result: str = "*",
    moves: int = 0,
    console: Console | None = None,
    colour: str | None = None,
    pace: float = 1.0,
) -> None:
    console = console or Console()
    colour = colour or detect_colour()
    lines = outro_lines(result, moves)
    if not console.is_terminal:
        for ln in lines:
            console.print(ln)
        return
    letters = scenes.use_letters()
    try:
        with Live(
            console=console, screen=True, auto_refresh=False, transient=True
        ) as live:
            for fr in king_frames(letters):
                live.update(_center(fr, console, colour), refresh=True)
                time.sleep(0.18 * pace)
            block = Text(
                "\n".join(king_frames(letters)[-1].plain.splitlines()[-2:]),
                style=scenes.AMBER,
            )
            msg = Text.assemble(
                block, "\n\n", (lines[0], "bold white"), "\n", (lines[1], scenes.CYAN)
            )
            live.update(_center(msg, console, colour), refresh=True)
            time.sleep(1.0 * pace)
    finally:
        with contextlib.suppress(Exception):
            console.clear()
            console.show_cursor(True)


def _center(text: Text, console: Console, colour: str) -> Text:
    if colour == "mono":
        text = Text(text.plain)
    pad_y = max(0, (console.size.height - text.plain.count("\n") - 1) // 2)
    width = max((len(ln) for ln in text.plain.splitlines()), default=0)
    pad_x = max(0, (console.size.width - width) // 2)
    out = Text("\n" * pad_y)
    for i, line in enumerate(text.split("\n")):
        out.append(" " * pad_x)
        out.append_text(line)
        if i < len(text.split("\n")) - 1:
            out.append("\n")
    return out


# ------------------------------------------------------------- knight spinner
_RING = [(0, 0), (0, 2), (0, 4), (1, 4), (2, 4), (2, 2), (2, 0), (1, 0)]


def knight_spinner_frames(letters: bool | None = None) -> list[Text]:
    """A knight stepping round a 3x3 ring (each cell 2 columns wide)."""
    letters = scenes.use_letters() if letters is None else letters
    knight = "N" if letters else "♞"
    frames = []
    for r, c in _RING:
        rows = [[" "] * 5 for _ in range(3)]
        for rr, cc in _RING:
            rows[rr][cc] = "·"
        rows[r][c] = knight
        frames.append(Text("\n".join("".join(row) for row in rows), style=scenes.CYAN))
    return frames


async def with_knight_spinner(
    awaitable: Awaitable[T], console: Console | None = None, threshold: float = 0.3
) -> T:
    """Await ``awaitable``; show the spinner only if it takes longer than threshold."""
    console = console or Console()
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait({task}, timeout=threshold)
    if task in done or not console.is_terminal:
        return await task
    frames = knight_spinner_frames()
    with Live(console=console, transient=True, auto_refresh=False) as live:
        i = 0
        while not task.done():
            live.update(
                Text.assemble(frames[i % len(frames)], "\n  warming up the engine"),
                refresh=True,
            )
            i += 1
            await asyncio.wait({task}, timeout=0.09)
    return await task


def help_header() -> str:
    return "   ♞    JARVIS CHESS\n  /_\\   A game of kings, sir. Shall we?\n [___]\n"
