"""ASCII cinematic: every scene renders at both sizes; timing, skip and NO_COLOR."""

import io

import chess
import pytest
from rich.console import Console
from rich.text import Text

from games.chess.intro import outro, player, scenes

SIZES = [(80, 24), (200, 60)]


@pytest.mark.parametrize("scene", scenes.SCENES, ids=lambda s: s.name)
@pytest.mark.parametrize(("w", "h"), SIZES)
def test_every_scene_renders_first_middle_and_last_frame(scene, w, h):
    for lt in (0.0, scene.duration / 2, scene.duration):
        t = scene.fn(lt, w, h, "truecolor")
        assert isinstance(t, Text)
        lines = t.plain.split("\n")
        assert len(lines) == h and all(len(ln) == w for ln in lines)
        assert t.plain.strip() or lt == 0.0  # not blank after the first instant


@pytest.mark.parametrize(
    ("w", "h"), [(20, 10), (40, 12), (79, 23), (120, 30), (300, 80)]
)
def test_scenes_never_crash_at_odd_sizes(w, h):
    for t in [i * 0.37 for i in range(0, 38)]:
        scenes.frame(t, w, h)


def test_total_duration_is_about_14_seconds():
    assert abs(scenes.total_duration() - 14.0) <= 0.5
    ends = [s.end for s in scenes.SCENES]
    assert ends == sorted(ends) and scenes.SCENES[0].start == 0.0
    # scenes tile the timeline with no gaps
    for a, b in zip(scenes.SCENES, scenes.SCENES[1:], strict=False):
        assert a.end == b.start


def test_seven_scenes_in_the_specified_order():
    assert [s.name for s in scenes.SCENES] == [
        "cold boot",
        "arc reactor",
        "grid",
        "pieces",
        "skirmish",
        "title",
        "handover",
    ]
    bounds = [(s.start, s.end) for s in scenes.SCENES]
    assert bounds == [
        (0, 2),
        (2, 4),
        (4, 6.5),
        (6.5, 8.5),
        (8.5, 11),
        (11, 13),
        (13, 14),
    ]


def test_timeline_maps_global_time_to_scenes():
    assert scenes.scene_at(0.1)[0].name == "cold boot"
    assert scenes.scene_at(5.0)[0].name == "grid"
    assert scenes.scene_at(9.9)[0].name == "skirmish"
    assert scenes.scene_at(99)[0].name == "handover"
    for t in (0, 1, 3, 5, 7, 9, 12, 13.5, 14):
        assert player.render_timeline(t, 80, 24).plain


def test_boot_lines_and_stockfish_status(monkeypatch):
    scenes._stockfish_present.cache_clear()
    monkeypatch.setattr(scenes.shutil, "which", lambda n: "/usr/bin/stockfish")
    late = scenes.scene_boot(1.95, 80, 24).plain
    assert (
        "JARVIS GAMES SUBSYSTEM v1.0" in late
        and late.count("[ OK ]") == 4
        and "WARN" not in late
    )
    scenes._stockfish_present.cache_clear()
    monkeypatch.setattr(scenes.shutil, "which", lambda n: None)
    assert "[ WARN ]" in scenes.scene_boot(1.95, 80, 24).plain
    scenes._stockfish_present.cache_clear()


def test_warn_is_amber_and_ok_is_cyan(monkeypatch):
    scenes._stockfish_present.cache_clear()
    monkeypatch.setattr(scenes.shutil, "which", lambda n: None)
    t = scenes.scene_boot(1.95, 80, 24, "truecolor")
    styles = {str(s.style) for s in t.spans}
    assert any(scenes.AMBER in s for s in styles) and any(
        scenes.CYAN in s for s in styles
    )
    scenes._stockfish_present.cache_clear()


def test_grid_shows_all_64_square_codes():
    plain = scenes.scene_grid(2.4, 80, 24).plain
    for f in "abcdefgh":
        for r in "12345678":
            assert f + r in plain


def test_pieces_drop_in_pawns_first_then_back_rank():
    early = scenes.scene_drop(0.45, 80, 24).plain
    done = scenes.scene_drop(1.99, 80, 24).plain
    assert "♙" in early and "♖" not in early
    assert done.count("♙") == 8 and done.count("♟") == 8 and "♔" in done and "♚" in done


def test_pieces_fall_back_to_letters(monkeypatch):
    monkeypatch.setenv("JARVIS_INTRO_ASCII", "1")
    done = scenes.scene_drop(1.99, 80, 24).plain
    assert "♙" not in done and "K" in done and "N" in done


def test_skirmish_is_a_legal_game_with_a_capture():
    boards = scenes.skirmish_boards()
    assert len(boards) == len(scenes.SKIRMISH) + 1
    b = chess.Board()
    for u in scenes.SKIRMISH:
        assert chess.Move.from_uci(u) in b.legal_moves
        b.push(chess.Move.from_uci(u))
    sans = chess.Board()
    out = []
    for u in scenes.SKIRMISH[:6]:
        out.append(sans.san(chess.Move.from_uci(u)))
        sans.push(chess.Move.from_uci(u))
    assert out == ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6"]  # the specified opening
    assert chess.Board().is_capture(chess.Move.from_uci("e2e4")) is False
    capture_ply = scenes.SKIRMISH.index("f3e5")
    assert boards[capture_ply].is_capture(chess.Move.from_uci("f3e5"))
    # shards drift after the capture lands
    t = (capture_ply * scenes.PLY_T) + scenes.MOVE_T + 0.04
    assert any(ch in scenes.scene_skirmish(t, 80, 24).plain for ch in "*+")


def test_title_has_banner_glitch_and_typed_subtitle():
    banner = scenes.scene_title(1.0, 120, 40).plain
    assert "█" in banner or "_" in banner or "|" in banner
    assert scenes.SUBTITLE not in scenes.scene_title(0.5, 120, 40).plain
    assert scenes.SUBTITLE in scenes.scene_title(2.0, 120, 40).plain
    glitch = [scenes.scene_title(0.35 + k / 30, 120, 40).plain for k in range(3)]
    assert len(set(glitch)) >= 2  # rows are offset between the three frames


def test_handover_collapses_to_a_dot_and_launch_line():
    assert "> launching board" in scenes.scene_handover(0.5, 80, 24).plain
    last = scenes.scene_handover(1.0, 80, 24).plain
    assert last.count("●") == 1


def test_compact_card_for_small_terminals():
    t = scenes.compact_card(40, 10)
    assert "JARVIS CHESS" in t.plain and scenes.SUBTITLE[:20] in t.plain


def test_no_color_gives_mono_output():
    t = scenes.frame(9.0, 80, 24, "mono")
    assert not t.spans and "\x1b" not in t.plain
    colored = scenes.frame(9.0, 80, 24, "truecolor")
    assert colored.spans


def test_colour_detection(monkeypatch):
    assert (
        player.detect_colour({"COLORTERM": "truecolor", "TERM": "xterm"}) == "truecolor"
    )
    assert player.detect_colour({"COLORTERM": "24bit"}) == "truecolor"
    assert player.detect_colour({"TERM": "xterm-256color"}) == "256"
    assert player.detect_colour({"TERM": "dumb"}) == "mono"
    assert player.detect_colour({"NO_COLOR": "1", "COLORTERM": "truecolor"}) == "mono"


def test_palette_matches_the_hud():
    assert (
        scenes.CYAN == "#22d3ee"
        and scenes.AMBER == "#f59e0b"
        and scenes.RED == "#ef4444"
        and scenes.NAVY == "#01050e"
    )


def test_frames_are_25_to_30_fps_and_skip_works():
    frames = list(player.run_frames(80, 24, fps=28))
    assert abs(len(frames) / scenes.total_duration() - 28) < 1.0
    # any key: jump to the end card and stop
    skipped = list(player.run_frames(80, 24, fps=28, skip_at=3.0))
    assert (
        skipped[-1][1].plain == scenes.end_card(80, 24).plain and skipped[-1][0] >= 3.0
    )
    # esc: straight to the board (no end card at all)
    esc = list(player.run_frames(80, 24, fps=28, skip_at=3.0, skip_kind="esc"))
    assert esc[-1][0] < 3.0


def test_intro_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("JARVIS_NO_INTRO", "1")
    assert player.intro_disabled()
    assert player.run() == player.SKIPPED_TO_BOARD
    monkeypatch.delenv("JARVIS_NO_INTRO")
    assert not player.intro_disabled()


def test_non_terminal_skips_straight_to_the_board():
    assert (
        player.run(console=Console(file=io.StringIO(), force_terminal=False))
        == player.SKIPPED_TO_BOARD
    )


def test_small_terminal_shows_only_the_compact_card(monkeypatch):
    shown = []
    console = Console(force_terminal=True, width=60, height=15, file=io.StringIO())
    monkeypatch.setattr(player.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        player,
        "_Keys",
        type(
            "K",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda s, *a: None,
                "poll": lambda s: None,
            },
        ),
    )
    orig = scenes.compact_card
    monkeypatch.setattr(
        scenes, "compact_card", lambda *a, **k: shown.append(1) or orig(*a, **k)
    )
    assert player.run(console=console, colour="mono") == player.FINISHED
    assert shown


def test_terminal_state_is_restored_even_on_ctrl_c(monkeypatch):
    console = Console(force_terminal=True, width=100, height=30, file=io.StringIO())
    state = {"cursor": []}
    real_show = console.show_cursor
    console.show_cursor = lambda show=True: (
        state["cursor"].append(show),
        real_show(show),
    )[1]
    monkeypatch.setattr(
        player,
        "_Keys",
        type(
            "K",
            (),
            {
                "__enter__": lambda s: s,
                "__exit__": lambda s, *a: None,
                "poll": lambda s: None,
            },
        ),
    )
    calls = {"n": 0}

    def boom(_s):
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(player.time, "sleep", boom)
    with pytest.raises(KeyboardInterrupt):
        player.run(console=console, colour="mono")
    assert state["cursor"][-1] is True  # cursor shown again


# ------------------------------------------------------------------ outro etc.
def test_outro_frames_and_text():
    frames = outro.king_frames()
    assert len(frames) >= 3 and all(isinstance(f, Text) for f in frames)
    lines = outro.outro_lines("1-0", 31)
    assert (
        lines[0] == "Good game, sir."
        and "White wins" in lines[1]
        and "31 moves" in lines[1]
    )
    assert "1 move." in outro.outro_lines("*", 1)[1]


def test_outro_prints_plainly_without_a_terminal():
    buf = Console(file=io.StringIO(), force_terminal=False)
    outro.run_outro("0-1", 20, console=buf)
    assert "Good game, sir." in buf.file.getvalue()


def test_knight_spinner_walks_a_ring():
    frames = outro.knight_spinner_frames(letters=False)
    assert len(frames) == 8
    assert all(f.plain.count("♞") == 1 for f in frames)
    assert len({f.plain for f in frames}) == 8
    assert all(f.plain.count("\n") == 2 for f in frames)  # 3 rows


async def test_spinner_only_shows_for_slow_startup():
    import asyncio

    console = Console(file=io.StringIO(), force_terminal=False)

    async def quick():
        return 1

    async def slow():
        await asyncio.sleep(0.4)
        return 2

    assert await outro.with_knight_spinner(quick(), console) == 1
    assert await outro.with_knight_spinner(slow(), console) == 2


def test_help_header_is_a_tiny_knight_and_title():
    h = outro.help_header()
    assert "JARVIS CHESS" in h and "♞" in h and h.count("\n") <= 4
