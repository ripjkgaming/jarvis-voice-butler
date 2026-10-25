"""The cheat sheets are generated from the live catalogue and must not drift."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gen_chess_docs  # noqa: E402

from games.chess.shortcuts import SHORTCUTS  # noqa: E402


def test_command_sheet_is_up_to_date():
    assert (
        ROOT / "docs/chess/COMMANDS.md"
    ).read_text() == gen_chess_docs.build_commands()


def test_keyboard_sheet_is_up_to_date():
    assert (
        ROOT / "docs/chess/KEYBOARD.md"
    ).read_text() == gen_chess_docs.build_keyboard()


def test_frontend_shortcut_table_matches_python():
    ts = (ROOT / "frontend/components/chess/dialogs.tsx").read_text()
    block = ts[
        ts.index("export const SHORTCUTS") : ts.index(
            "export function ShortcutsOverlay"
        )
    ]
    pairs = re.findall(r"\[\s*'([^']*)',\s*'([^']*)'\s*,?\s*\]", block)
    assert pairs == [(k, v) for k, v in SHORTCUTS]


def test_sheet_mentions_the_key_phrases():
    text = gen_chess_docs.build_commands()
    for phrase in (
        "knight to f three",
        "castle kingside",
        "stop",
        "take over",
        "keep analysing",
        "hey jarvis".title(),
    ):
        assert phrase.lower() in text.lower(), phrase
    assert len(text.splitlines()) < 220  # one page-ish
