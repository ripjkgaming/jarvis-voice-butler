"""The guarantee: chess never touches LiveKit, Gemini or the network (unasked)."""

import asyncio
import json
import pathlib
import random
import socket
import subprocess
import sys
import time

import chess
import chess_audio as ca
import numpy as np
import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"

IMPORT_ALL = r"""
import importlib, pkgutil, sys
import games
bad = []
names = []
for m in pkgutil.walk_packages(games.__path__, "games."):
    names.append(m.name)
    try:
        importlib.import_module(m.name)
    except ImportError as exc:  # optional extras (textual) may be missing
        bad.append((m.name, str(exc)))
for extra in ("audio_shared", "wake_events", "games_launch"):
    importlib.import_module(extra)
leaked = sorted(k for k in sys.modules if k == "livekit" or k.startswith("livekit.") or k == "google.genai" or k.startswith("google.genai") or k == "google_genai")
print("MODULES", len(names))
print("LEAKED", leaked)
print("BAD", [b for b in bad if "textual" not in b[1]])
"""


def test_importing_every_games_module_pulls_in_no_livekit_or_gemini():
    out = subprocess.run(
        [sys.executable, "-c", IMPORT_ALL],
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(SRC),
            "PATH": "/usr/bin",
            "HOME": str(pathlib.Path.home()),
        },
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    lines = dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)
    assert int(lines["MODULES"]) >= 25
    assert lines["LEAKED"].strip() == "[]", lines["LEAKED"]
    assert lines["BAD"].strip() == "[]", lines["BAD"]


def test_no_games_source_mentions_livekit_or_genai():
    for path in (SRC / "games").rglob("*.py"):
        text = path.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert "livekit" not in stripped and "google.genai" not in stripped, (
                    path,
                    line,
                )


def test_chess_never_sends_a_mute_command():
    for path in (SRC / "games").rglob("*.py"):
        text = path.read_text()
        assert '"mute":' not in text and "'mute':" not in text, path
        assert "set_muted" not in text, path


# ------------------------------------------------------------ the network guard
class NetGuard:
    def __init__(self, monkeypatch):
        self.calls: list[str] = []
        real_connect = socket.socket.connect

        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                host = address[0]
                if host not in ("127.0.0.1", "::1", "localhost"):
                    self.calls.append(f"{host}")
            return real_connect(sock, address)

        real_gai = socket.getaddrinfo

        def gai(host, *a, **k):
            if host not in ("127.0.0.1", "::1", "localhost", None):
                self.calls.append(f"dns:{host}")
            return real_gai(host, *a, **k)

        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(socket, "getaddrinfo", gai)


@pytest.mark.slow
@pytest.mark.skipif(not ca.piper_available(), reason="Piper voice not installed")
async def test_scripted_twenty_move_voice_game_uses_no_network(tmp_path, monkeypatch):
    """Fake mic -> real Silero VAD -> real whisper -> parser, Piper output captured."""
    import audio_shared
    from games.chess import templates, voice_moves
    from games.chess.engine import MiniEngine
    from games.chess.game import ChessGame
    from games.chess.settings import ChessSettings
    from games.voice.engine import VoiceEngine
    from games.voice.stt import LocalSTT
    from games.voice.tts import MemorySink, PiperSpeaker

    guard = NetGuard(monkeypatch)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    game = ChessGame(
        ChessSettings(announce_moves=True, difficulty="novice", confirm_moves=False),
        engine=MiniEngine(4),
        save_dir=tmp_path,
        autosave=False,
        ai_delay=0.0,
    )
    stt = LocalSTT("base.en")
    stt.load()
    sink = MemorySink(realtime=False)
    speaker = PiperSpeaker(voice=ca._voice(), sink=sink)
    speaker.load()
    eng = VoiceEngine(game, stt=stt, speaker=speaker, capture=None, chime=False)
    await game.new_game()
    await eng.start(load_models=False)
    rng = random.Random(21)
    played = 0
    heard_ok = 0
    attempts = 0
    while played < 20 and attempts < 60 and not game.state.is_over():
        attempts += 1
        for _ in range(200):
            if game.state.board.turn == game.state.human_colour and not game.thinking:
                break
            await asyncio.sleep(0.02)
        for _ in range(200):  # let Jarvis finish his one-line announcement first
            if not speaker.speaking and eng._speech_q.empty():
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.3)
        b = game.state.board
        cands = []
        for m in b.legal_moves:
            phrase = (
                templates.announce_move(b, m)
                .rstrip(".")
                .replace(", en passant", "")
                .split(", promoting")[0]
            )
            if b.is_castling(m):
                continue
            pr = voice_moves.parse_move(phrase, b)
            if pr.kind == "move" and pr.moves[0] == m:
                cands.append((m, phrase))
        if not cands:
            break
        m, phrase = rng.choice(cands)
        before = game.state.move_count
        eng.feed_audio(
            ca.with_padding(ca.speech(phrase), lead=0.3, tail=0.7), time.monotonic()
        )
        for _ in range(400):
            await asyncio.sleep(0.02)
            if game.state.move_count > before:
                break
        played += 1
        heard_ok += (
            game.state.move_count > before and game.state.board.move_stack[before] == m
        )
    await game.drain(5)
    assert played >= 15, played
    assert heard_ok >= 0.8 * played, (heard_ok, played, eng.transcripts, eng.dropped)
    assert guard.calls == [], guard.calls  # not one packet left the machine
    assert sink.chunks, "Piper output was captured"
    assert not game.badges and game.panel is None and game.snapshot()["eval"] is None
    await eng.stop()
    await game.close()
    _ = (np, json, audio_shared, chess)


async def test_openrouter_is_the_only_host_and_only_when_asked(tmp_path, monkeypatch):
    import httpx

    from games.chess.commentary import Commentary, OpenRouter, Quota
    from games.chess.engine import MiniEngine
    from games.chess.game import ChessGame
    from games.chess.settings import ChessSettings

    hosts = []

    def handler(req: httpx.Request):
        hosts.append(req.url.host)
        payload = {
            "headline": "Ok",
            "spoken": "A fine move, Sir.",
            "detail": "It is solid.",
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )

    router = OpenRouter(
        ["m/x:free"],
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        quota=Quota(tmp_path / "q.json"),
        key="sk-or-1",
    )
    game = ChessGame(
        ChessSettings(announce_moves=False, difficulty="novice"),
        engine=MiniEngine(1),
        commentary=Commentary(router),
        save_dir=tmp_path,
        autosave=False,
    )
    game.listening = True
    await game.new_game()
    await game.handle_text("e4")
    await game.drain(5)
    for _ in range(100):
        if game.state.board.turn == game.state.human_colour:
            break
        await asyncio.sleep(0.02)
    assert hosts == []
    await game.handle_text("explain my move")
    await game.drain(15)
    assert hosts == ["openrouter.ai"]
    await game.close()
