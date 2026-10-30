"""Wire frames for the voice WebSocket transport (docs/voice-transport.md).

Binary: ``[1-byte kind][payload]``. Text: JSON ``{"topic": str, "payload": any}``.
Malformed or oversize frames decode to ``None`` and bump ``dropped``.
"""

from __future__ import annotations

import collections
import json
import struct
from typing import Any

KIND_MIC = 0x01
KIND_AUDIO = 0x02
KIND_VIDEO = 0x03
KINDS = frozenset({KIND_MIC, KIND_AUDIO, KIND_VIDEO})
MAX_FRAME_BYTES = 64 * 1024
VIDEO_HEADER_BYTES = 8
_JPEG_SOI = b"\xff\xd8"
_TOPIC_MAX = 128
BARGE_TOPIC = "jarvis.voice.barge.v1"
BARGE_MAX_BYTES = 256
_SEQ_MAX = 1_000_000_000

dropped: collections.Counter[str] = collections.Counter()


def encode_binary(kind: int, payload: bytes) -> bytes:
    if kind not in KINDS:
        raise ValueError("unknown frame kind")
    if len(payload) + 1 > MAX_FRAME_BYTES:
        raise ValueError("frame too large")
    return bytes([kind]) + bytes(payload)


def decode_binary(data: object) -> tuple[int, bytes] | None:
    if not isinstance(data, (bytes, bytearray)) or not data:
        dropped["malformed"] += 1
        return None
    if len(data) > MAX_FRAME_BYTES:
        dropped["oversize"] += 1
        return None
    kind, payload = data[0], bytes(data[1:])
    if kind not in KINDS:
        dropped["kind"] += 1
        return None
    if kind in (KIND_MIC, KIND_AUDIO):
        if not payload or len(payload) % 2:
            dropped["malformed"] += 1
            return None
    elif _video_parts(payload) is None:
        dropped["malformed"] += 1
        return None
    return kind, payload


def _video_parts(payload: bytes) -> tuple[int, bytes] | None:
    if len(payload) < VIDEO_HEADER_BYTES + 2:
        return None
    (ts_ms,) = struct.unpack(">Q", payload[:VIDEO_HEADER_BYTES])
    jpeg = payload[VIDEO_HEADER_BYTES:]
    if not jpeg.startswith(_JPEG_SOI):
        return None
    return ts_ms, jpeg


def encode_video(ts_ms: int, jpeg: bytes) -> bytes:
    if type(ts_ms) is not int or not 0 <= ts_ms < 2**63:
        raise ValueError("invalid timestamp")
    return encode_binary(KIND_VIDEO, struct.pack(">Q", ts_ms) + bytes(jpeg))


def decode_video(payload: bytes) -> tuple[int, bytes] | None:
    parts = _video_parts(payload)
    if parts is None:
        dropped["malformed"] += 1
    return parts


def validate_barge_payload(payload: object) -> bool:
    """Counter-only barge packets, <= 256 B serialized (src/voice_barge_signals.py)."""
    if not isinstance(payload, dict):
        return False
    kind = payload.get("kind")
    if kind not in {"user_start", "reply_start"}:
        return False
    user_seq = payload.get("user_seq")
    if type(user_seq) is not int or not 0 <= user_seq <= _SEQ_MAX:
        return False
    if kind == "reply_start":
        reply_seq = payload.get("reply_seq")
        if type(reply_seq) is not int or not 0 < reply_seq <= _SEQ_MAX:
            return False
    try:
        size = len(json.dumps(payload, separators=(",", ":")).encode("ascii"))
    except (TypeError, ValueError):
        return False
    return size <= BARGE_MAX_BYTES


def _topic_ok(topic: object) -> bool:
    return isinstance(topic, str) and 0 < len(topic) <= _TOPIC_MAX


def encode_text(topic: str, payload: Any) -> str:
    if not _topic_ok(topic):
        raise ValueError("invalid topic")
    if topic == BARGE_TOPIC and not validate_barge_payload(payload):
        raise ValueError("invalid barge payload")
    text = json.dumps({"topic": topic, "payload": payload}, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError("frame too large")
    return text


def decode_text(data: object) -> tuple[str, Any] | None:
    if isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_FRAME_BYTES:
            dropped["oversize"] += 1
            return None
        try:
            data = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            dropped["malformed"] += 1
            return None
    if not isinstance(data, str):
        dropped["malformed"] += 1
        return None
    if len(data.encode("utf-8")) > MAX_FRAME_BYTES:
        dropped["oversize"] += 1
        return None
    try:
        item = json.loads(data)
    except ValueError:
        dropped["malformed"] += 1
        return None
    if not isinstance(item, dict) or not _topic_ok(item.get("topic")):
        dropped["malformed"] += 1
        return None
    topic, payload = item["topic"], item.get("payload")
    if topic == BARGE_TOPIC and not validate_barge_payload(payload):
        dropped["malformed"] += 1
        return None
    return topic, payload
