import json

import pytest

from wake_client import (
    downsample_48k_to_16k,
    load_livekit_env,
    mint_summon_token,
    summon_room_name,
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


def test_wake_client_refuses_without_picovoice_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wake_client import WakeClient

    monkeypatch.delenv("PICOVOICE_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PICOVOICE_ACCESS_KEY"):
        WakeClient()._check_config()
