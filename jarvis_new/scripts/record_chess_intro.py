#!/usr/bin/env python3
"""Render the ASCII intro to an asciinema v2 cast (no terminal needed).

    uv run python scripts/record_chess_intro.py docs/chess/intro.cast
Play it with `asciinema play docs/chess/intro.cast`.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from rich.console import Console  # noqa: E402

from games.chess.intro import player  # noqa: E402


def main() -> None:
    out = Path(
        sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "chess" / "intro.cast"
    )
    w, h, fps = 80, 24, 20
    events = []
    t_last = None
    for t, frame in player.run_frames(w, h, "truecolor", fps=fps):
        buf = io.StringIO()
        Console(
            file=buf, force_terminal=True, width=w, height=h, color_system="truecolor"
        ).print(frame, end="")
        events.append(
            [round(t, 3), "o", "\x1b[H\x1b[2J" + buf.getvalue().replace("\n", "\r\n")]
        )
        t_last = t
    events.append(
        [
            round((t_last or 0) + 0.5, 3),
            "o",
            "\x1b[0m\r\n♞ Jarvis Chess running — press Ctrl+C here to quit.\r\n",
        ]
    )
    header = {
        "version": 2,
        "width": w,
        "height": h,
        "timestamp": 1790000000,
        "title": "Jarvis Chess intro",
        "env": {"TERM": "xterm-256color"},
    }
    with out.open("w") as fh:
        fh.write(json.dumps(header) + "\n")
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    print(f"wrote {out} ({len(events)} frames)")


if __name__ == "__main__":
    main()
