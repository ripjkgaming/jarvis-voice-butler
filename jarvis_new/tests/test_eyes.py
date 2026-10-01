"""Tests for Eyes (src/eyes.py). Hermetic: synthetic landmarks, fake
detectors and a fake Gemini opener — no camera, no network, no models."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

import eyes


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis"))


def upright() -> dict:
    """Upright head: ears above their shoulders, level shoulders. Pure."""
    return {
        eyes.NOSE: (0.50, 0.25),
        eyes.LEFT_EAR: (0.39, 0.30),
        eyes.RIGHT_EAR: (0.61, 0.30),
        eyes.LEFT_SHOULDER: (0.40, 0.50),
        eyes.RIGHT_SHOULDER: (0.60, 0.50),
        eyes.LEFT_WRIST: (0.30, 0.80),
        eyes.RIGHT_WRIST: (0.70, 0.80),
    }


def test_posture_metrics_upright() -> None:
    m = eyes.posture_metrics(upright())
    assert m is not None
    assert m["head_forward"] == pytest.approx(0.05)
    assert m["shoulder_slope"] == pytest.approx(0.0)
    assert m["nose_y"] == pytest.approx(0.25)


def test_posture_metrics_missing_landmark() -> None:
    lm = upright()
    del lm[eyes.NOSE]
    assert eyes.posture_metrics(lm) is None
    assert eyes.posture_metrics({}) is None


def test_slouching_triggers() -> None:
    base = eyes.posture_metrics(upright())
    assert base is not None
    assert not eyes.slouching(base, base)
    # Head jutting far forward: absolute ceiling.
    assert eyes.slouching({**base, "head_forward": 0.5}, base)
    # Moderate drift vs baseline.
    assert eyes.slouching({**base, "head_forward": base["head_forward"] + 0.2}, base)
    # Just under the margin: fine.
    assert not eyes.slouching(
        {**base, "head_forward": base["head_forward"] + 0.05}, base
    )
    # Lopsided shoulders.
    assert eyes.slouching({**base, "shoulder_slope": 0.4}, base)
    # Nose dropped (head hanging forward).
    assert eyes.slouching({**base, "nose_y": base["nose_y"] + 0.1}, base)


def test_average_metrics() -> None:
    assert eyes.average_metrics([]) is None
    a = eyes.posture_metrics(upright())
    assert a is not None
    assert eyes.average_metrics([a, a]) == a


def _det(label, score, box) -> dict:
    return {"label": label, "score": score, "box": box}


def test_phone_in_hand() -> None:
    near = _det("cell phone", 0.8, (0.25, 0.75, 0.35, 0.85))  # by left wrist
    far = _det("cell phone", 0.8, (0.80, 0.05, 0.90, 0.15))
    wrists = [(0.30, 0.80), (0.70, 0.80)]
    assert eyes.phone_in_hand([near], wrists)
    assert not eyes.phone_in_hand([far], wrists)
    assert eyes.phone_in_hand([far], [])  # no pose: anywhere counts
    assert not eyes.phone_in_hand(
        [_det("cell phone", 0.2, (0.25, 0.75, 0.35, 0.85))], wrists
    )
    assert not eyes.phone_in_hand([_det("cup", 0.9, (0.25, 0.75, 0.35, 0.85))], wrists)
    assert not eyes.phone_in_hand([], wrists)


def test_sustain_tracker_fires_once_per_stretch() -> None:
    t = eyes.SustainTracker(20.0)
    assert not t.update(True, 0.0)
    assert not t.update(True, 19.9)
    assert t.update(True, 20.0)
    assert not t.update(True, 25.0)  # re-armed: needs a fresh stretch
    assert not t.update(False, 26.0)
    assert not t.update(True, 27.0)
    assert t.update(True, 47.0)


def test_wrists_from() -> None:
    assert len(eyes.wrists_from(upright())) == 2
    assert eyes.wrists_from({}) == []


def test_gemini_body_and_parse() -> None:
    url, body = eyes.build_gemini_body(
        b"\xff\xd8fake", "does this tie match?", "gemini-3.8-flash"
    )
    assert "gemini-3.8-flash:generateContent" in url
    data = json.loads(body)
    parts = data["contents"][0]["parts"]
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
    assert "does this tie match?" in parts[0]["text"]
    reply = {"candidates": [{"content": {"parts": [{"text": "  Sharp suit, Sir. "}]}}]}
    assert eyes.parse_gemini_text(reply) == "Sharp suit, Sir."
    assert eyes.parse_gemini_text({}) == ""
    assert eyes.parse_gemini_text({"candidates": []}) == ""


def test_gemini_model_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JARVIS_TEXT_MODEL", raising=False)
    assert eyes.gemini_model() == "gemini-3.8-flash"
    monkeypatch.setenv("JARVIS_TEXT_MODEL", "gemini-3.7-flash")
    assert eyes.gemini_model() == "gemini-3.7-flash"


class _Resp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def _gemini_opener(text: str):
    def open_fn(req, timeout=None):
        assert "image/jpeg" in req.data.decode()
        return _Resp({"candidates": [{"content": {"parts": [{"text": text}]}}]})

    return open_fn


def _bgr_frame():
    return np.full((240, 320, 3), 128, dtype=np.uint8)


def test_describe_appearance_happy(home, monkeypatch: pytest.MonkeyPatch) -> None:
    import camera_hub

    monkeypatch.setattr(camera_hub, "snapshot", lambda who, timeout=4.0: _bgr_frame())
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-key")
    got = eyes.describe_appearance(
        "does this tie match?", opener=_gemini_opener("Sharp, Sir.")
    )
    assert got == "Sharp, Sir."


def test_describe_appearance_no_key(home, monkeypatch: pytest.MonkeyPatch) -> None:
    import camera_hub

    monkeypatch.setattr(camera_hub, "snapshot", lambda who, timeout=4.0: _bgr_frame())
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    got = eyes.describe_appearance()
    assert "keys.env" in got


def test_describe_appearance_no_frame(home, monkeypatch: pytest.MonkeyPatch) -> None:
    import camera_hub

    monkeypatch.setattr(camera_hub, "snapshot", lambda who, timeout=4.0: None)
    assert "couldn't see you" in eyes.describe_appearance()


def _fake_pose(lm: dict):
    points = [SimpleNamespace(x=0.0, y=0.0) for _ in range(33)]
    for idx, (x, y) in lm.items():
        points[idx] = SimpleNamespace(x=x, y=y)

    class _Pose:
        def detect(self, image):
            return SimpleNamespace(pose_landmarks=[points])

    return _Pose()


def test_calibrate_with_injected_frame(home) -> None:
    base = eyes.calibrate(frame=_bgr_frame(), pose=_fake_pose(upright()))
    assert base is not None
    assert base["head_forward"] == pytest.approx(0.05)
    assert eyes.load_baseline() == base


def test_monitor_slouch_speaks_after_sustain(home) -> None:
    slouched = upright()
    slouched[eyes.LEFT_EAR] = (0.30, 0.32)  # head jutting forward
    slouched[eyes.RIGHT_EAR] = (0.70, 0.32)
    said: list = []
    clock = [1000.0]

    mon = eyes.EyesMonitor(
        frame=_bgr_frame,
        pose=_fake_pose(slouched),
        detector=None,
        speaker=lambda text, source: said.append((source, text)),
        now=lambda: clock[0],
        slouch_s=20.0,
    )
    base = eyes.posture_metrics(upright())
    assert base is not None
    for _ in range(5):
        mon.poll(base)
        clock[0] += 5.0
    assert said and said[0][0] == "posture"
    assert eyes.status()["slouch_alerts"] == 1


def test_monitor_phone_alert(home) -> None:
    said: list = []
    clock = [2000.0]

    class _Det:
        def detect(self, image):
            # Pixels on the 320x240 fake frame, next to the left wrist.
            box = SimpleNamespace(origin_x=80, origin_y=170, width=40, height=60)
            cat = SimpleNamespace(category_name="cell phone", score=0.9)
            return SimpleNamespace(
                detections=[SimpleNamespace(categories=[cat], bounding_box=box)]
            )

    mon = eyes.EyesMonitor(
        frame=_bgr_frame,
        pose=_fake_pose(upright()),
        detector=_Det(),
        speaker=lambda text, source: said.append((source, text)),
        now=lambda: clock[0],
        phone_s=6.0,
    )
    for _ in range(4):
        mon.poll()
        clock[0] += 2.5
    assert said and said[0][0] == "phone"


def test_status_shape(home) -> None:
    st = eyes.status()
    assert st["slouch_alerts"] == 0
    assert st["calibrated"] is False
    assert st["running"] is False
