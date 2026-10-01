"""Tests for mint_token.py summon/join modes (needs `livekit` installed)."""

import base64
import json
import subprocess
import sys

import pytest

livekit = pytest.importorskip("livekit.api")

sys.path.insert(0, "src")

from mint_token import valid_room_name  # noqa: E402


def _mint(*args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, "src/mint_token.py", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=="))


def test_valid_room_name_allowlist() -> None:
    assert valid_room_name("jarvis-123")
    assert not valid_room_name("")
    assert not valid_room_name("../../etc")
    assert not valid_room_name("a b")
    assert not valid_room_name("x" * 65)
    assert not valid_room_name(None)


def test_summon_mode_dispatches_agent() -> None:
    out = _mint()
    assert out["roomName"].startswith("voice_assistant_room_")
    assert "roomConfig" in _claims(out["participantToken"])


def test_join_mode_names_room_without_dispatch() -> None:
    out = _mint("my-agent", "jarvis-999", "join")
    assert out["roomName"] == "jarvis-999"
    assert "roomConfig" not in _claims(out["participantToken"])
