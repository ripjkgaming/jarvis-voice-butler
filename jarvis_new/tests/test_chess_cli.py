"""`chess` command: every flag, the PATH-conflict check and a clean Ctrl+C."""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from games import cli

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"


def parse(*argv):
    return cli.build_parser().parse_args(list(argv))


def test_defaults():
    a = parse()
    assert not (
        a.no_intro
        or a.intro_only
        or a.tui
        or a.voice
        or a.mic_test
        or a.resume
        or a.spectate
    )
    assert a.fen is None and a.level is None and a.colour is None and a.time is None


@pytest.mark.parametrize(
    ("argv", "attr", "value"),
    [
        (["--no-intro"], "no_intro", True), (["--intro-only"], "intro_only", True), (["--tui"], "tui", True),
        (["--voice"], "voice", True), (["--mic-test"], "mic_test", True), (["--resume"], "resume", True),
        (["--spectate"], "spectate", True), (["--fen", "8/8/8/8/8/8/8/K6k w - - 0 1"], "fen", "8/8/8/8/8/8/8/K6k w - - 0 1"),
        (["--level", "expert"], "level", "expert"), (["--colour", "black"], "colour", "black"),
        (["--color", "random"], "colour", "random"), (["--time", "10+0"], "time", "10+0"),
        (["--time", "untimed"], "time", "untimed"), (["--time", "3+2"], "time", "3+2"), (["--time", "7"], "time", "7+0"),
        (["--listen"], "listen", True),
    ],
)  # fmt: skip
def test_every_flag_parses(argv, attr, value):
    assert getattr(parse(*argv), attr) == value


@pytest.mark.parametrize(
    "level", ["novice", "club", "strong", "expert", "master", "full"]
)
def test_levels(level):
    assert parse("--level", level).level == level


@pytest.mark.parametrize("colour", ["white", "black", "random"])
def test_colours(colour):
    assert parse("--colour", colour).colour == colour


@pytest.mark.parametrize(
    "argv",
    [["--level", "godlike"], ["--colour", "green"], ["--time", "abc"], ["--bogus"]],
)
def test_bad_usage_exits_2(argv, capsys):
    assert cli.main(argv) == cli.EXIT_USAGE


def test_help_prints_the_knight_header_and_exit_codes(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert (
        "JARVIS CHESS" in out and "A game of kings, sir" in out and "exit codes" in out
    )
    for flag in (
        "--no-intro",
        "--intro-only",
        "--tui",
        "--voice",
        "--mic-test",
        "--resume",
        "--fen",
        "--level",
        "--colour",
        "--time",
        "--spectate",
    ):
        assert flag in out


def test_bad_fen_is_a_usage_error(capsys):
    assert cli.main(["--fen", "not a fen"]) == cli.EXIT_USAGE
    assert "bad --fen" in capsys.readouterr().err


class Calls:
    def __init__(self, monkeypatch, session_code=0):
        self.order = []
        monkeypatch.setattr(
            cli,
            "_play_intro",
            lambda mode: self.order.append(f"intro:{mode}") or "finished",
        )

        async def fake_session(args):
            self.order.append("session")
            self.args = args
            return session_code, {"result": "*", "moves": 0}

        monkeypatch.setattr(cli, "_session", fake_session)
        import games.chess.intro.outro as outro

        monkeypatch.setattr(
            outro, "run_outro", lambda *a, **k: self.order.append("outro")
        )


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVIS_NO_INTRO", raising=False)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))


def test_default_plays_intro_then_the_board(monkeypatch):
    c = Calls(monkeypatch)
    assert cli.main([]) == 0
    assert c.order == ["intro:full", "session", "outro"]


def test_no_intro_flag_and_env(monkeypatch):
    c = Calls(monkeypatch)
    assert cli.main(["--no-intro"]) == 0
    assert c.order == ["session", "outro"]
    monkeypatch.setenv("JARVIS_NO_INTRO", "1")
    c.order.clear()
    cli.main([])
    assert c.order == ["session", "outro"]


def test_intro_only_stops_after_the_intro(monkeypatch):
    c = Calls(monkeypatch)
    assert cli.main(["--intro-only"]) == 0
    assert c.order == ["intro:full"]


def test_voice_mode_skips_the_intro_and_outro(monkeypatch):
    c = Calls(monkeypatch)
    cli.main(["--voice"])
    assert c.order == ["session"] and c.args.voice


def test_settings_can_shorten_or_disable_the_intro(monkeypatch, tmp_path):
    import json

    c = Calls(monkeypatch)
    (tmp_path / "chess.json").write_text(json.dumps({"intro_length": "short"}))
    cli.main([])
    assert c.order[0] == "intro:short"
    c.order.clear()
    (tmp_path / "chess.json").write_text(json.dumps({"intro": False}))
    cli.main([])
    assert c.order[0] == "session"


def test_mic_test_and_tui_dispatch(monkeypatch):
    seen = []

    async def fake_mic(*a, **k):
        seen.append("mic")
        return 0

    import games.voice.mic_test as mt

    monkeypatch.setattr(mt, "run_mic_test", fake_mic)
    assert cli.main(["--mic-test"]) == 0 and seen == ["mic"]
    import games.tui as tui

    monkeypatch.setattr(tui, "run_tui", lambda args: seen.append("tui") or 0)
    assert cli.main(["--tui"]) == 0 and seen[-1] == "tui"


def test_exit_code_follows_the_session(monkeypatch):
    Calls(monkeypatch, session_code=cli.EXIT_VOICE_UNAVAILABLE)
    assert cli.main(["--no-intro", "--voice"]) == cli.EXIT_VOICE_UNAVAILABLE


def test_ctrl_c_returns_130_and_restores_the_cursor(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "_play_intro", lambda mode: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    assert cli.main([]) == cli.EXIT_INTERRUPTED
    assert "\x1b[?25h" in capsys.readouterr().out


def test_missing_dependency_is_exit_3(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_check_deps", lambda: "websockets")
    assert cli.main([]) == cli.EXIT_MISSING_DEP
    assert "uv sync --extra chess" in capsys.readouterr().err


def test_time_arg_normalisation():
    assert cli.time_arg("5") == "5+0" and cli.time_arg("untimed") == "untimed"
    with pytest.raises(argparse.ArgumentTypeError):
        cli.time_arg("fast")


# ------------------------------------------------------- a real process, real SIGINT
def _run_cli(tmp_path, *args):
    env = {
        **os.environ,
        "JARVIS_HOME": str(tmp_path),
        "PYTHONPATH": str(SRC),
        "JARVIS_NO_INTRO": "1",
    }
    return subprocess.Popen(
        [sys.executable, "-m", "games.cli", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(tmp_path),
    )  # fmt: skip


def test_real_process_ctrl_c_is_clean(tmp_path):
    p = _run_cli(tmp_path, "--no-intro", "--no-window", "--port", "0", "--ui-port", "0")
    try:
        out = ""
        deadline = time.time() + 30
        while time.time() < deadline:
            line = p.stdout.readline()
            out += line
            if "Jarvis Chess running" in line:
                break
        assert "Jarvis Chess running — press Ctrl+C here to quit." in out
        p.send_signal(signal.SIGINT)
        code = p.wait(timeout=20)
        err = p.stderr.read()
        assert "Traceback" not in err, err
        assert code == cli.EXIT_INTERRUPTED
        assert not (tmp_path / "games" / "chess.lock").exists()  # lock released
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=20)
        p.stdout.close()
        p.stderr.close()


def test_real_process_help_and_bad_flag(tmp_path):
    p = _run_cli(tmp_path, "--help")
    out, _err = p.communicate(timeout=30)
    assert p.returncode == 0 and "JARVIS CHESS" in out
    p = _run_cli(tmp_path, "--level", "nope")
    p.communicate(timeout=30)
    assert p.returncode == 2


# ------------------------------------------------------------ install script
def _install(tmp_path, path_dirs, *args, assume_no=True):
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": ":".join([*map(str, path_dirs), "/usr/bin", "/bin"]),
        "CHESS_BIN_DIR": str(tmp_path / "bin"),
    }
    if assume_no:
        env["CHESS_ASSUME_NO"] = "1"
    return subprocess.run(
        ["bash", str(REPO / "scripts" / "install-chess-command.sh"), *args],
        capture_output=True, text=True, env=env, timeout=30, stdin=subprocess.DEVNULL,
    )  # fmt: skip


def test_install_symlinks_when_the_name_is_free(tmp_path):
    r = _install(tmp_path, [])
    assert r.returncode == 0, r.stderr
    link = tmp_path / "bin" / "chess"
    assert (
        link.is_symlink() and link.resolve() == (REPO / "scripts" / "chess").resolve()
    )
    assert (
        _install(tmp_path, [tmp_path / "bin"]).returncode == 0
    )  # idempotent: it owns the name


def test_install_warns_and_offers_jchess_when_the_name_is_taken(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    fake = other / "chess"
    fake.write_text("#!/bin/sh\necho some other chess\n")
    fake.chmod(0o755)
    r = _install(tmp_path, [other])
    assert r.returncode == 1
    assert "already exists" in r.stderr and "jchess" in r.stderr
    assert not (tmp_path / "bin" / "chess").exists()
    r = _install(tmp_path, [other], "--name", "jchess")
    assert r.returncode == 0 and (tmp_path / "bin" / "jchess").is_symlink()


def test_launcher_script_runs_help(tmp_path):
    r = subprocess.run(
        [str(REPO / "scripts" / "chess"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0 and "JARVIS CHESS" in r.stdout
