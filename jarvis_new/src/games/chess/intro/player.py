"""Play the intro in the terminal: rich Live on the alternate screen.

Any key skips to the end card; Esc skips straight to the board; Ctrl+C
restores the terminal and then propagates. ``JARVIS_NO_INTRO=1`` disables.
"""

from __future__ import annotations

import contextlib
import os
import select
import sys
import time
from collections.abc import Callable, Iterator

from rich.console import Console
from rich.live import Live
from rich.text import Text

from games.chess.intro import scenes

FINISHED = "finished"
SKIPPED_TO_END = "skipped_to_end"
SKIPPED_TO_BOARD = "skipped_to_board"

MIN_W, MIN_H = 80, 24
HOLD_END_CARD_S = 0.6


def intro_disabled() -> bool:
    return os.environ.get("JARVIS_NO_INTRO", "").strip() in {"1", "true", "yes"}


def detect_colour(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    if env.get("NO_COLOR") is not None and env.get("NO_COLOR") != "":
        return "mono"
    if env.get("COLORTERM", "").lower() in {"truecolor", "24bit"}:
        return "truecolor"
    if "256" in env.get("TERM", ""):
        return "256"
    return "mono" if env.get("TERM", "") in {"dumb", ""} else "256"


def render_timeline(
    t: float, width: int, height: int, colour: str = "truecolor"
) -> Text:
    return scenes.frame(t, width, height, colour)


def run_frames(
    width: int = 80,
    height: int = 24,
    colour: str = "truecolor",
    fps: int = 28,
    skip_at: float | None = None,
    skip_kind: str = "any",
) -> Iterator[tuple[float, Text]]:
    """Deterministic frame generator (no tty, no sleeping) for tests."""
    total = scenes.total_duration()
    n = int(total * fps)
    for i in range(n + 1):
        t = i / fps
        if skip_at is not None and t >= skip_at:
            if skip_kind == "esc":
                return
            yield t, scenes.end_card(width, height, colour)
            return
        yield t, render_timeline(t, width, height, colour)


class _Keys:
    """Non-blocking raw key reader on stdin (cbreak), restored on exit."""

    def __init__(self) -> None:
        self.fd = None
        self._old = None

    def __enter__(self) -> _Keys:
        if sys.stdin.isatty():
            import termios
            import tty

            self.fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        return self

    def __exit__(self, *exc) -> None:
        if self.fd is not None and self._old is not None:
            import termios

            with contextlib.suppress(Exception):
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self._old)

    def poll(self) -> str | None:
        """Return "esc", "key" or None."""
        if self.fd is None:
            return None
        r, _, _ = select.select([self.fd], [], [], 0)
        if not r:
            return None
        data = os.read(self.fd, 32)
        if not data:
            return None
        if data == b"\x1b":
            return "esc"
        return "key"


def run(
    *,
    console: Console | None = None,
    fps: int = 28,
    colour: str | None = None,
    bell: bool = False,
    skip_keys: bool = True,
    on_done: Callable[[], None] | None = None,
    start_at: float = 0.0,
) -> str:
    """Play the cinematic. Returns finished | skipped_to_end | skipped_to_board."""
    if intro_disabled():
        return SKIPPED_TO_BOARD
    console = console or Console()
    colour = colour or detect_colour()
    if not console.is_terminal:
        return SKIPPED_TO_BOARD
    w, h = console.size
    outcome = FINISHED
    try:
        with (
            _Keys() as keys,
            Live(
                console=console,
                screen=True,
                auto_refresh=False,
                transient=True,
                redirect_stdout=False,
                redirect_stderr=False,
            ) as live,
        ):
            console.show_cursor(False)
            if w < MIN_W or h < MIN_H:
                live.update(scenes.compact_card(w, h, colour), refresh=True)
                time.sleep(1.5)
            else:
                total = scenes.total_duration()
                t0 = time.monotonic() - start_at
                belled = False
                end_hold: float | None = None
                while True:
                    now = time.monotonic() - t0
                    if skip_keys:
                        k = keys.poll()
                        if k == "esc":
                            outcome = SKIPPED_TO_BOARD
                            break
                        if k == "key" and end_hold is None:
                            outcome = SKIPPED_TO_END
                            end_hold = now
                    if end_hold is not None:
                        live.update(scenes.end_card(w, h, colour), refresh=True)
                        if now - end_hold >= HOLD_END_CARD_S:
                            break
                    else:
                        if bell and not belled and now >= scenes.TITLE_SCENE.start:
                            belled = True
                            console.bell()
                        if now >= total:
                            break
                        live.update(render_timeline(now, w, h, colour), refresh=True)
                    time.sleep(max(0.0, 1.0 / fps - 0.002))
    finally:
        with contextlib.suppress(Exception):
            console.show_cursor(True)
        if on_done:
            on_done()
    return outcome


SHORT_START = 9.8  # "short" intro: the last ~4 s of the cinematic
