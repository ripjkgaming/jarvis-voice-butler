"""``chess`` command: intro -> Classic Mode board, or ``--voice`` hands-free.

Exit codes: 0 ok, 1 error, 2 usage, 3 missing dependency, 4 voice engine
unavailable, 130 interrupted (Ctrl+C). The terminal is always restored.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import sys

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_MISSING_DEP = 3
EXIT_VOICE_UNAVAILABLE = 4
EXIT_INTERRUPTED = 130

LEVELS = ["novice", "club", "strong", "expert", "master", "full"]
COLOURS = ["white", "black", "random"]
_TIME_RX = re.compile(r"^(untimed|\d+\+\d+|\d+)$")

EPILOG = """\
exit codes: 0 ok · 1 error · 2 usage · 3 missing dependency (uv sync --extra chess)
            4 voice engine unavailable · 130 interrupted (Ctrl+C)
Nothing is analysed, commented or explained unless you ask. Say or press Stop
(Esc) and it all stops instantly.
"""


def time_arg(value: str) -> str:
    if not _TIME_RX.match(value):
        raise argparse.ArgumentTypeError(
            "use untimed, 5+0, 10+0, 15+10 or MINUTES+INCREMENT (e.g. 3+2)"
        )
    return value if value in ("untimed",) or "+" in value else f"{value}+0"


def build_parser() -> argparse.ArgumentParser:
    from games.chess.intro.outro import help_header

    p = argparse.ArgumentParser(
        prog="chess",
        description=help_header(),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--no-intro", action="store_true", help="skip straight to the board")
    p.add_argument("--intro-only", action="store_true", help="just play the intro")
    p.add_argument(
        "--tui", action="store_true", help="play inside the terminal (mouse works)"
    )
    p.add_argument(
        "--voice",
        action="store_true",
        help="Voice Mode: hands-free on the local voice engine (no Jarvis or LiveKit needed)",
    )
    p.add_argument(
        "--mic-test",
        action="store_true",
        help="speak a few moves, see transcripts and timings",
    )
    p.add_argument("--resume", action="store_true", help="load the last autosave")
    p.add_argument("--fen", metavar="FEN", help="start from a position")
    p.add_argument("--level", choices=LEVELS, help="opponent strength")
    p.add_argument(
        "--colour", "--color", dest="colour", choices=COLOURS, help="your colour"
    )
    p.add_argument(
        "--time", type=time_arg, metavar="TC", help="untimed|5+0|10+0|15+10|custom"
    )
    p.add_argument("--spectate", action="store_true", help="Jarvis vs Jarvis")
    p.add_argument(
        "--listen", action="store_true", help="Classic Mode with voice listening on"
    )
    p.add_argument("--no-window", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--port", type=int, default=4320, help=argparse.SUPPRESS)
    p.add_argument("--ui-port", type=int, default=4321, help=argparse.SUPPRESS)
    return p


def _check_deps() -> str | None:
    for mod in ("chess", "rich", "websockets", "httpx"):
        try:
            __import__(mod)
        except ImportError:
            return mod
    return None


def validate_fen(fen: str) -> str | None:
    import chess

    try:
        chess.Board(fen)
    except ValueError as exc:
        return str(exc)
    return None


def _want_intro(args: argparse.Namespace, settings) -> str:
    """full | short | off"""
    if args.voice or args.no_intro:
        return "off"
    if os.environ.get("JARVIS_NO_INTRO", "").strip() in {"1", "true", "yes"}:
        return "off"
    if not settings.intro or settings.intro_length == "off":
        return "off"
    return "short" if settings.intro_length == "short" else "full"


def _play_intro(mode: str) -> str:
    from games.chess.intro import player

    return player.run(start_at=player.SHORT_START if mode == "short" else 0.0)


async def _session(args: argparse.Namespace) -> tuple[int, dict]:
    from games.app import RunOptions, run_session
    from games.chess.engine import make_engine
    from games.chess.intro.outro import with_knight_spinner

    engine = await with_knight_spinner(make_engine())
    opts = RunOptions(
        mode="voice" if args.voice else "classic",
        fen=args.fen,
        level=args.level,
        colour=args.colour,
        time=args.time,
        resume=args.resume,
        spectate=args.spectate,
        listen=args.listen,
        no_window=args.no_window,
        port=args.port,
        ui_port=args.ui_port,
        engine=engine,
    )
    return await run_session(opts)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    if args.fen:
        err = None
        missing = _check_deps()
        if not missing:
            err = validate_fen(args.fen)
        if err:
            print(f"chess: bad --fen: {err}", file=sys.stderr)
            return EXIT_USAGE
    missing = _check_deps()
    if missing:
        print(
            f"chess: missing Python package '{missing}'. Run: uv sync --extra chess",
            file=sys.stderr,
        )
        return EXIT_MISSING_DEP
    try:
        return _run(args)
    except KeyboardInterrupt:
        with contextlib.suppress(Exception):
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
        return EXIT_INTERRUPTED


def _run(args: argparse.Namespace) -> int:
    from games.chess.settings import load as load_settings

    if args.mic_test:
        from games.voice.mic_test import run_mic_test

        return asyncio.run(run_mic_test())
    settings = load_settings()
    if args.intro_only:
        _play_intro("full")
        return EXIT_OK
    intro = _want_intro(args, settings)
    if intro != "off":
        _play_intro(intro)
    if args.tui:
        from games.tui import run_tui

        return run_tui(args)
    code, summary = asyncio.run(_session(args))
    if summary and not args.voice:
        from games.chess.intro.outro import run_outro

        run_outro(summary.get("result", "*"), summary.get("moves", 0))
    return code


def entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    entry()
