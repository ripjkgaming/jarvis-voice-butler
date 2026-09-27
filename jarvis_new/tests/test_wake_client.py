import json

import pytest

from wake_client import (
    OWW_FRAME,
    clear_hud_room,
    downsample_48k_to_16k,
    extract_talk_text,
    frame_16k_chunks,
    handle_mic_command,
    handle_talk_request,
    load_livekit_env,
    mint_summon_token,
    publish_hud_room,
    read_hud_room,
    room_has_active_call,
    score_frames,
    shell_talk_candidates,
    summon_overlay,
    summon_room_name,
    wake_score,
    wake_socket_path,
    wake_threshold,
)


def test_downsample_takes_every_third_sample() -> None:
    frame = list(range(1536))
    down = downsample_48k_to_16k(frame)
    assert len(down) == 512
    assert down[0] == 0
    assert down[1] == 3
    assert down[-1] == 1533


def test_downsample_short_frame_is_safe() -> None:
    assert downsample_48k_to_16k([10, 20, 30]) == [10]


def test_summon_room_names_are_unique() -> None:
    assert summon_room_name(now=1000.0) == "jarvis-1000"
    assert summon_room_name(now=1001.0) != summon_room_name(now=1000.0)


def test_load_livekit_env_reads_frontend_file(tmp_path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        'LIVEKIT_URL="wss://example.livekit.cloud"\n'
        "LIVEKIT_API_KEY=key123\n"
        "LIVEKIT_API_SECRET = secret456 \n"
        "AGENT_NAME=my-agent\n"
    )
    found = load_livekit_env(env_file)
    assert found == {
        "LIVEKIT_URL": "wss://example.livekit.cloud",
        "LIVEKIT_API_KEY": "key123",
        "LIVEKIT_API_SECRET": "secret456",
    }


def test_load_livekit_env_missing_file(tmp_path) -> None:
    assert load_livekit_env(tmp_path / "nope") == {}


def test_mint_summon_token_carries_dispatch() -> None:
    jwt = mint_summon_token(
        url="wss://example.livekit.cloud",
        api_key="test-key",
        api_secret="test-secret-" + "x" * 32,
        room="jarvis-1",
        agent_name="my-agent",
    )
    import base64

    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload))
    assert claims["video"]["room"] == "jarvis-1"
    assert claims["video"]["roomJoin"] is True
    assert claims["roomConfig"]["agents"][0]["agentName"] == "my-agent"


def test_frame_16k_chunks_buffers_to_native_frames() -> None:
    frames, pending = frame_16k_chunks([], [1] * (OWW_FRAME * 2 + 100))
    assert len(frames) == 2
    assert all(len(f) == OWW_FRAME for f in frames)
    assert pending == [1] * 100
    # Remainder carries over: no audio dropped between mic blocks.
    frames, pending = frame_16k_chunks(pending, [2] * (OWW_FRAME - 100))
    assert len(frames) == 1
    assert frames[0] == [1] * 100 + [2] * (OWW_FRAME - 100)
    assert pending == []


def test_wake_score_reads_model_key() -> None:
    assert wake_score({"hey_jarvis": 0.7}) == 0.7
    assert wake_score({}) == 0.0
    assert wake_score({"other": 0.9}) == 0.0


def test_wake_threshold_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JARVIS_WAKE_THRESHOLD", "0.8")
    assert wake_threshold() == 0.8
    monkeypatch.setenv("JARVIS_WAKE_THRESHOLD", "nonsense")
    assert wake_threshold() == 0.5


def test_wake_client_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from wake_client import WakeClient

    # No third-party key exists anymore; config is LiveKit creds only.
    monkeypatch.delenv("PICOVOICE_ACCESS_KEY", raising=False)
    client = WakeClient()
    monkeypatch.setattr(
        client,
        "_creds",
        {
            "LIVEKIT_URL": "wss://x",
            "LIVEKIT_API_KEY": "k",
            "LIVEKIT_API_SECRET": "s",
        },
    )
    client._check_config()  # must not raise


def test_wake_client_refuses_without_livekit_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wake_client import WakeClient

    client = WakeClient()
    monkeypatch.setattr(client, "_creds", {})
    with pytest.raises(RuntimeError, match="LIVEKIT_URL"):
        client._check_config()


def test_room_has_active_call_needs_user_plus_agent() -> None:
    assert room_has_active_call([]) is False
    assert room_has_active_call(["jarvis-master"]) is False
    assert room_has_active_call(["jarvis-master", "agent-AJ_123"]) is True


async def test_summon_stays_out_when_call_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wake_client
    from wake_client import WakeClient

    async def _busy(_creds: dict) -> bool:
        return True

    monkeypatch.setattr(wake_client, "active_call_exists", _busy)
    client = WakeClient()
    # Must return before touching livekit.rtc (unavailable/blocked here).
    await client._summon_session()


def test_shell_talk_candidates_end_with_talk() -> None:
    for argv in shell_talk_candidates():
        assert argv[-1] == "talk"
    assert len(shell_talk_candidates()) >= 1


def test_summon_overlay_never_raises() -> None:
    # Fire-and-forget daemon thread; must not raise even with no shell.
    summon_overlay()


async def test_summon_joins_without_deferral(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Wake joins immediately now: the HUD is receive-only (no mic in the
    webview), so there is no race to defer — one call check, then join."""
    import wake_client
    from wake_client import WakeClient

    class _ReachedConnectError(Exception):
        pass

    class _FakeRoom:
        def on(self, *args, **kwargs):
            def deco(fn):
                return fn

            return deco

        async def connect(self, *args, **kwargs):
            raise _ReachedConnectError("reached-connect")

    calls = {"n": 0}

    async def _idle(_creds: dict) -> bool:
        calls["n"] += 1
        return False

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setattr(wake_client, "active_call_exists", _idle)
    import livekit.rtc

    monkeypatch.setattr(livekit.rtc, "Room", _FakeRoom)
    client = WakeClient()
    with pytest.raises(_ReachedConnectError):
        await client._summon_session()
    assert calls["n"] == 1


def test_hud_room_publish_read_clear(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert read_hud_room() is None
    assert publish_hud_room("jarvis-123") is True
    assert read_hud_room() == "jarvis-123"
    clear_hud_room()
    assert read_hud_room() is None


def test_hud_room_stale_reads_as_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import os
    import time

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert publish_hud_room("jarvis-old") is True
    path = tmp_path / "hud_room"
    old = time.time() - 20 * 60
    os.utime(path, (old, old))
    assert read_hud_room() is None


async def test_active_call_exists_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wake_client import active_call_exists

    assert await active_call_exists({}) is False


# --- Phase 4.2: unix-socket mic mute control (no mic hardware needed) ---


def test_wake_socket_path_honors_jarvis_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    assert wake_socket_path() == tmp_path / "wake.sock"


def test_wake_socket_path_defaults_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JARVIS_HOME", raising=False)
    from pathlib import Path

    assert wake_socket_path() == Path.home() / ".jarvis" / "wake.sock"


def test_handle_mic_command_mute_flips_state() -> None:
    reply, new_muted = handle_mic_command(
        {"mute": True}, muted=False, threshold=0.5, in_call=False
    )
    assert reply == {"ok": True, "muted": True}
    assert new_muted is True
    reply, new_muted = handle_mic_command(
        {"mute": False}, muted=True, threshold=0.5, in_call=True
    )
    assert reply == {"ok": True, "muted": False}
    assert new_muted is False


def test_handle_mic_command_status_reports_state() -> None:
    reply, new_muted = handle_mic_command(
        {"status": True}, muted=True, threshold=0.7, in_call=True
    )
    assert reply == {
        "ok": True,
        "muted": True,
        "threshold": 0.7,
        "in_call": True,
    }
    assert new_muted is None  # status never flips the mute


def test_handle_talk_request_unmuted() -> None:
    reply, wants_talk, unmute = handle_talk_request({"talk": True}, muted=False)
    assert reply == {"ok": True, "talk": "requested", "muted": False}
    assert wants_talk is True
    assert unmute is False  # already live: nothing to unmute


def test_handle_talk_request_muted_unmutes_first() -> None:
    # PTT is an explicit talk action: holding it while muted still summons.
    reply, wants_talk, unmute = handle_talk_request({"talk": True}, muted=True)
    assert reply == {"ok": True, "talk": "requested", "muted": False}
    assert wants_talk is True
    assert unmute is True


def test_handle_talk_request_rejects_garbage() -> None:
    for bad in ({"talk": False}, {"talk": "yes"}, {"frobnicate": 1}, [1], "talk", None):
        reply, wants_talk, unmute = handle_talk_request(bad, muted=False)
        assert reply["ok"] is False
        assert wants_talk is False
        assert unmute is False


def test_extract_talk_text_accepts_short_text() -> None:
    assert (
        extract_talk_text({"talk": True, "text": "  tell me the news "})
        == "tell me the news"
    )
    assert extract_talk_text({"talk": True, "text": "x" * 500}) == "x" * 500


def test_extract_talk_text_rejects_blank_long_and_nonstr() -> None:
    assert extract_talk_text({"talk": True}) is None
    assert extract_talk_text({"talk": True, "text": "   "}) is None
    assert extract_talk_text({"talk": True, "text": 42}) is None
    assert extract_talk_text({"talk": True, "text": "x" * 501}) is None
    assert extract_talk_text("talk") is None
    assert extract_talk_text(None) is None


def test_handle_mic_command_rejects_garbage() -> None:
    reply, new_muted = handle_mic_command(
        {"mute": "yes"}, muted=False, threshold=0.5, in_call=False
    )
    assert reply["ok"] is False and new_muted is None
    reply, new_muted = handle_mic_command(
        {"frobnicate": 1}, muted=False, threshold=0.5, in_call=False
    )
    assert reply["ok"] is False and new_muted is None
    reply, new_muted = handle_mic_command(
        ["mute"],
        muted=False,
        threshold=0.5,
        in_call=False,  # type: ignore[arg-type]
    )
    assert reply["ok"] is False and new_muted is None


def _rpc(sock_path, payload: dict) -> dict:
    import json
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as cli:
        cli.settimeout(5.0)
        cli.connect(str(sock_path))
        cli.sendall(json.dumps(payload).encode())
        chunks = []
        while True:
            data = cli.recv(4096)
            if not data:
                break
            chunks.append(data)
    return json.loads(b"".join(chunks).decode())


def test_mic_control_socket_round_trip(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import time

    from wake_client import WakeClient

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    client = WakeClient()
    thread = client.start_mic_control()
    assert thread.daemon is True
    deadline = time.time() + 5
    while not wake_socket_path().exists() and time.time() < deadline:
        time.sleep(0.05)
    assert _rpc(wake_socket_path(), {"mute": True}) == {"ok": True, "muted": True}
    assert client.muted is True
    status = _rpc(wake_socket_path(), {"status": True})
    assert status["ok"] is True and status["muted"] is True
    assert status["threshold"] == client._threshold
    assert status["in_call"] is False
    assert _rpc(wake_socket_path(), {"mute": False}) == {"ok": True, "muted": False}
    assert client.muted is False


def test_mic_control_survives_stale_socket_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import time

    from wake_client import WakeClient

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    # A dead listener's leftover file: must be unlinked, not fatal.
    wake_socket_path().write_text("stale")
    client = WakeClient()
    client.start_mic_control()
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            reply = _rpc(wake_socket_path(), {"status": True})
            break
        except OSError:
            time.sleep(0.05)
    else:
        raise AssertionError("listener never came up over stale file")
    assert reply["ok"] is True


class _StreamModel:
    """Records every frame; openWakeWord is streaming and needs them all."""

    def __init__(self) -> None:
        self.frames = []

    def predict(self, frame):
        self.frames.append(frame.copy())
        return {"hey_jarvis": 0.0}


def test_score_frames_feeds_every_frame_including_silence() -> None:
    # Regression: a perf gate skipped silent frames and 2 of every 3 voiced
    # ones; the model then saw chopped audio and "hey Jarvis" scored 0.00
    # (vs 1.00 at full rate) -- the wake word never fired.
    import numpy as np

    audio = np.concatenate(
        [
            np.zeros(OWW_FRAME * 3, dtype=np.int16),  # digital silence
            np.full(OWW_FRAME * 4, 4000, dtype=np.int16),  # voiced
            np.arange(100, dtype=np.int16),  # partial frame stays pending
        ]
    )
    model = _StreamModel()
    scores, rest = score_frames(model, audio)
    assert len(scores) == len(model.frames) == 7
    assert np.array_equal(np.concatenate(model.frames), audio[: OWW_FRAME * 7])
    assert np.array_equal(rest, np.arange(100, dtype=np.int16))
