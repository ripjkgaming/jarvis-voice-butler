"""Tests for mint_token.py: WebSocket call tokens for summon/join/phone modes.

No LiveKit SDK: tokens are HMAC-signed with $JARVIS_HOME/voice_secret.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "src")

import mint_token
from jarvis_rt.transport.tokens import (
    BadTokenError,
    MemoryNonceLedger,
    load_secret,
    mint_call_token,
    valid_room_name,
    verify_call_token,
)
from mint_token import valid_room_name as mint_valid_room_name

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def home(tmp_path, monkeypatch):
    base = tmp_path / "jarvis"
    monkeypatch.setenv("JARVIS_HOME", str(base))
    monkeypatch.delenv("JARVIS_VOICE_PORT", raising=False)
    monkeypatch.delenv("JARVIS_VOICE_URL", raising=False)
    return base


def _verify(payload: dict, home: Path):
    return verify_call_token(
        payload["participantToken"], load_secret(home), ledger=MemoryNonceLedger()
    )


def test_valid_room_name_allowlist() -> None:
    assert valid_room_name("jarvis-123")
    assert mint_valid_room_name("jarvis-123")
    assert not mint_valid_room_name("")
    assert not mint_valid_room_name("../../etc")
    assert not mint_valid_room_name("a b")
    assert not mint_valid_room_name("x" * 65)
    assert not mint_valid_room_name(None)


def test_summon_mode_is_speaker_with_roundtrip(home) -> None:
    out = mint_token.mint_token_payload()
    assert out["roomName"].startswith("voice_assistant_room_")
    assert out["participantName"] == "user"
    assert out["serverUrl"] == "ws://127.0.0.1:7880/voice"
    claims = _verify(out, home)
    assert claims.mode == "speaker"
    assert claims.room == out["roomName"]
    assert claims.expires - claims.issued == 120
    assert claims.attrs == {}


def test_join_mode_is_observer_without_second_dispatch(home) -> None:
    out = mint_token.mint_token_payload("jarvis-999", False, "my-agent")
    assert out["roomName"] == "jarvis-999"
    claims = _verify(out, home)
    assert claims.mode == "observer"
    assert claims.room == "jarvis-999"


def test_bad_room_name_is_rejected_by_token_layer(home) -> None:
    with pytest.raises(ValueError):
        mint_call_token("../../etc", "u", "speaker", 60, None, load_secret(home))


def test_bad_room_name_falls_back_to_random_in_mint(home) -> None:
    out = mint_token.mint_token_payload("../../etc", True)
    assert out["roomName"].startswith("voice_assistant_room_")


def test_join_with_bad_room_cli_falls_back_to_summon(home, capsys) -> None:
    assert mint_token.main(["mint_token.py", "a", "../../etc", "join"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["roomName"].startswith("voice_assistant_room_")
    assert _verify(out, home).mode == "speaker"


def test_private_phone_token_uses_phone_ttl_and_owner_attrs(home) -> None:
    import phone_session

    out = mint_token.mint_token_payload(private_phone=True)
    claims = _verify(out, home)
    assert phone_session.private_room(out["roomName"])
    assert claims.room == out["roomName"]
    assert claims.mode == "speaker"
    assert claims.expires - claims.issued == 900
    assert claims.attrs["jarvis.phone_owner"] == out["roomName"]
    assert claims.attrs["jarvis.phone_sig"]
    assert claims.identity == phone_session.identity_for(out["roomName"])


def test_private_phone_rooms_are_unique(home) -> None:
    a = mint_token.mint_token_payload(private_phone=True)
    b = mint_token.mint_token_payload(private_phone=True)
    assert a["roomName"] != b["roomName"]


def test_phone_call_token_carries_call_attr_and_phone_ttl(home, monkeypatch) -> None:
    import phone_calls

    class _Queue:
        def accepted(self, call_id, now, consume=False):
            return True

    monkeypatch.setattr(phone_calls, "queue", lambda: _Queue())
    call_id = "a" * 32
    out = mint_token.mint_token_payload("", True, phone_call_id=call_id)
    claims = _verify(out, home)
    assert claims.attrs["jarvis.phone_call"] == call_id
    assert claims.identity == "jarvis-phone-" + call_id
    assert claims.expires - claims.issued == 900


def test_phone_call_rejected_when_not_accepted(home, monkeypatch) -> None:
    import phone_calls

    class _Queue:
        def accepted(self, call_id, now, consume=False):
            return False

    monkeypatch.setattr(phone_calls, "queue", lambda: _Queue())
    with pytest.raises(RuntimeError):
        mint_token.mint_token_payload("", True, phone_call_id="b" * 32)


def test_private_call_refuses_room_or_observer(home) -> None:
    with pytest.raises(ValueError):
        mint_token.mint_token_payload("jarvis-1", True, private_phone=True)
    with pytest.raises(ValueError):
        mint_token.mint_token_payload("", False, private_phone=True)


def test_missing_secret_is_auto_created_never_raises(home) -> None:
    assert not (home / "voice_secret").exists()
    out = mint_token.mint_token_payload()
    secret_file = home / "voice_secret"
    assert secret_file.is_file()
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
    assert _verify(out, home).mode == "speaker"


def test_token_signed_by_other_secret_is_rejected(home) -> None:
    out = mint_token.mint_token_payload()
    with pytest.raises(BadTokenError):
        verify_call_token(out["participantToken"], b"\x00" * 32)


def test_voice_url_overrides(home, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_VOICE_PORT", "9123")
    assert mint_token.voice_url() == "ws://127.0.0.1:9123/voice"
    monkeypatch.setenv("JARVIS_VOICE_URL", "wss://tunnel.example/voice")
    assert mint_token.mint_token_payload()["serverUrl"] == "wss://tunnel.example/voice"
    monkeypatch.setenv("JARVIS_VOICE_URL", "https://nope")
    assert mint_token.voice_url() == "ws://127.0.0.1:9123/voice"


def test_active_call_exists_reads_registry_file(home) -> None:
    assert mint_token.active_call_exists() is False
    home.mkdir(parents=True, exist_ok=True)
    (home / "voice_active.json").write_text(json.dumps({"active": True}))
    assert mint_token.active_call_exists() is True
    (home / "voice_active.json").write_text(json.dumps({"active": False}))
    assert mint_token.active_call_exists() is False
    (home / "voice_active.json").write_text("not json{")
    assert mint_token.active_call_exists() is False


def test_cli_prints_one_json_line(home) -> None:
    env = {**os.environ, "JARVIS_HOME": str(home)}
    env.pop("JARVIS_VOICE_URL", None)
    proc = subprocess.run(
        [sys.executable, str(SRC / "mint_token.py"), "my-agent", "jarvis-999", "join"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert set(out) == {"serverUrl", "roomName", "participantName", "participantToken"}
    assert out["serverUrl"] == "ws://127.0.0.1:7880/voice"
    assert _verify(out, home).mode == "observer"
