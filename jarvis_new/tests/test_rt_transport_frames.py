import pytest

from jarvis_rt.transport import frames
from jarvis_rt.transport.frames import (
    BARGE_TOPIC,
    KIND_AUDIO,
    KIND_MIC,
    KIND_VIDEO,
    MAX_FRAME_BYTES,
    decode_binary,
    decode_text,
    decode_video,
    encode_binary,
    encode_text,
    encode_video,
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 20


@pytest.fixture(autouse=True)
def _reset_counter():
    frames.dropped.clear()
    yield


def test_mic_and_audio_round_trip():
    pcm = bytes(range(256)) * 4
    assert decode_binary(encode_binary(KIND_MIC, pcm)) == (KIND_MIC, pcm)
    assert decode_binary(encode_binary(KIND_AUDIO, pcm)) == (KIND_AUDIO, pcm)


def test_odd_or_empty_pcm_is_malformed():
    assert decode_binary(bytes([KIND_MIC]) + b"abc") is None
    assert decode_binary(bytes([KIND_AUDIO])) is None
    assert frames.dropped["malformed"] == 2


def test_oversize_frame_dropped_and_counted():
    assert decode_binary(bytes([KIND_MIC]) + b"\x00" * MAX_FRAME_BYTES) is None
    assert frames.dropped["oversize"] == 1


def test_empty_and_unknown_kind_dropped():
    assert decode_binary(b"") is None
    assert decode_binary(bytes([0x09]) + b"\x00\x00") is None
    assert frames.dropped["kind"] == 1


def test_encode_binary_rejects_bad_kind_and_size():
    with pytest.raises(ValueError):
        encode_binary(0x09, b"\x00\x00")
    with pytest.raises(ValueError):
        encode_binary(KIND_MIC, b"\x00" * MAX_FRAME_BYTES)


def test_video_round_trip_and_bad_soi():
    frame = encode_video(1234, JPEG)
    kind, payload = decode_binary(frame)
    assert kind == KIND_VIDEO
    assert decode_video(payload) == (1234, JPEG)
    assert decode_binary(bytes([KIND_VIDEO]) + b"\x00" * 8 + b"NOPE") is None


def test_text_round_trip():
    text = encode_text("state", {"agent": "idle", "user": "listening"})
    assert decode_text(text) == ("state", {"agent": "idle", "user": "listening"})
    assert decode_text(text.encode()) == (
        "state",
        {"agent": "idle", "user": "listening"},
    )


@pytest.mark.parametrize(
    "data",
    ["not json", "[1,2]", '{"payload": 1}', '{"topic": "", "payload": 1}', b"\xff\xfe"],
)
def test_text_garbage_dropped(data):
    assert decode_text(data) is None
    assert frames.dropped["malformed"] >= 1


def test_text_oversize_dropped():
    big = '{"topic":"state","payload":"' + "x" * MAX_FRAME_BYTES + '"}'
    assert decode_text(big) is None
    assert frames.dropped["oversize"] == 1


def _barge(**extra):
    return {"kind": "user_start", "user_seq": 3, **extra}


def test_barge_valid_packets_accepted():
    assert decode_text(encode_text(BARGE_TOPIC, _barge())) is not None
    reply = {"kind": "reply_start", "user_seq": 3, "reply_seq": 1}
    assert decode_text(encode_text(BARGE_TOPIC, reply)) is not None


def test_barge_oversize_rejected_both_ways():
    big = _barge(pad="x" * 260)
    with pytest.raises(ValueError):
        encode_text(BARGE_TOPIC, big)
    import json

    raw = json.dumps({"topic": BARGE_TOPIC, "payload": big})
    assert decode_text(raw) is None


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "reply_start", "user_seq": 1, "reply_seq": 0},
        {"kind": "user_start", "user_seq": True},
        {"kind": "user_start", "user_seq": -1},
        {"kind": "other", "user_seq": 1},
        "user_start",
    ],
)
def test_barge_invalid_payloads_rejected(payload):
    with pytest.raises(ValueError):
        encode_text(BARGE_TOPIC, payload)


def test_non_barge_topic_accepts_any_json():
    assert decode_text(encode_text("lk.chat", {"text": "hi"})) == (
        "lk.chat",
        {"text": "hi"},
    )
