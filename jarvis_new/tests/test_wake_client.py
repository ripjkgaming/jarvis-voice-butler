import json

import pytest

from wake_client import (
    OWW_FRAME,
    downsample_48k_to_16k,
    frame_16k_chunks,
    load_livekit_env,
    mint_summon_token,
    room_has_active_call,
    summon_room_name,
    wake_score,
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


async def test_active_call_exists_fails_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wake_client import active_call_exists

    assert await active_call_exists({}) is False
