"""Eyes: posture + distraction watch over the shared camera hub.

Pose (mediapipe PoseLandmarker lite) and phone (efficientdet_lite0) run
on hub frames at ~2 fps while enabled. Sustained slouching or
phone-in-hand earns one dry butler remark via src/speak.py (the only
way background code talks out loud). Appearance feedback on request
goes through the free Gemini vision REST endpoint.

Fail-soft throughout: missing models or camera just means no alerts.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

#: Slouch must persist this long before Jarvis speaks.
SLOUCH_SUSTAIN_S = 20.0

#: Phone-in-hand sustain (stricter mid-focus, see focus_active()).
PHONE_SUSTAIN_S = 6.0
PHONE_SUSTAIN_FOCUS_S = 3.0

#: Detector score floor for "cell phone".
PHONE_SCORE = 0.5

#: Head-forward drift vs baseline that counts as slouching (fraction of
#: shoulder width), plus an absolute ceiling that always counts.
HEAD_MARGIN = 0.15
HEAD_MAX = 0.45

#: Lopsided shoulders beyond this (fraction of shoulder width) count too.
SLOPE_MAX = 0.25

#: Nose drop below baseline (fraction of frame height) that counts.
NOSE_DROP = 0.06

#: Monitor cadence.
FPS = 2.0

#: MediaPipe pose indices we need.
NOSE, LEFT_EAR, RIGHT_EAR = 0, 7, 8
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_WRIST, RIGHT_WRIST = 15, 16

SLOUCH_LINES = (
    "Sir, you're slouching.",
    "Straighten up a touch, Sir.",
    "Shoulders back, Sir — the chair misses you.",
    "Mind your posture, Sir.",
)

PHONE_LINES = (
    "Sir, eyes off the phone.",
    "The phone can wait, Sir.",
    "Sir — focus. The glowing rectangle will survive.",
)


def jarvis_home() -> Path:
    """Base dir honoring $JARVIS_HOME (default ~/.jarvis). Pure (env)."""
    home = os.environ.get("JARVIS_HOME", "").strip()
    return Path(home) if home else Path.home() / ".jarvis"


def models_dir() -> Path:
    """Vision .task/.tflite files (default ~/.jarvis/models/vision). Pure."""
    override = os.environ.get("JARVIS_VISION_MODELS", "").strip()
    if override:
        return Path(override)
    return jarvis_home() / "models" / "vision"


def state_dir() -> Path:
    """$JARVIS_HOME/eyes (created on demand). Never raises."""
    try:
        path = jarvis_home() / "eyes"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return jarvis_home() / "eyes"


def state_path() -> Path:
    """Stats + baseline file. Pure (env)."""
    return state_dir() / "state.json"


# --- pure posture metrics ------------------------------------------------


def posture_metrics(lm: dict[int, tuple[float, float]]) -> dict[str, float] | None:
    """Normalized landmarks -> {head_forward, shoulder_slope, nose_y}. Pure.

    head_forward: mean ear-vs-shoulder horizontal drift over shoulder
    width (grows as the head juts forward). shoulder_slope: vertical
    shoulder asymmetry over shoulder width. nose_y: raw normalized nose
    height (compared against the calibrated baseline separately).
    None when a needed landmark is missing or shoulders coincide.
    """
    try:
        ear_l = lm[LEFT_EAR]
        ear_r = lm[RIGHT_EAR]
        sh_l = lm[LEFT_SHOULDER]
        sh_r = lm[RIGHT_SHOULDER]
        nose = lm[NOSE]
        width = abs(sh_l[0] - sh_r[0])
        if width < 1e-6:
            return None
        head = (abs(ear_l[0] - sh_l[0]) + abs(ear_r[0] - sh_r[0])) / 2.0 / width
        slope = abs(sh_l[1] - sh_r[1]) / width
        return {"head_forward": head, "shoulder_slope": slope, "nose_y": nose[1]}
    except (KeyError, TypeError, IndexError):
        return None


def slouching(metrics: dict[str, float], baseline: dict[str, float]) -> bool:
    """Slouch vs the calibrated upright baseline? Pure."""
    try:
        if metrics["head_forward"] >= HEAD_MAX:
            return True
        if metrics["head_forward"] - baseline["head_forward"] >= HEAD_MARGIN:
            return True
        if metrics["shoulder_slope"] >= SLOPE_MAX:
            return True
        return metrics["nose_y"] - baseline["nose_y"] >= NOSE_DROP
    except (KeyError, TypeError):
        return False


def average_metrics(samples: list[dict[str, float]]) -> dict[str, float] | None:
    """Mean of metric dicts, or None when empty. Pure."""
    if not samples:
        return None
    keys = ("head_forward", "shoulder_slope", "nose_y")
    try:
        return {k: sum(s[k] for s in samples) / len(samples) for k in keys}
    except (KeyError, TypeError):
        return None


# --- pure phone logic ----------------------------------------------------


def _near_wrist(box: tuple[float, float, float, float], wrists: list) -> bool:
    # Box overlaps (or nearly touches) a wrist point.
    x0, y0, x1, y1 = box
    pad = 0.12
    return any(
        x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad for x, y in wrists
    )


def phone_in_hand(detections: list[dict], wrists: list[tuple[float, float]]) -> bool:
    """A confident 'cell phone' box near a wrist (or anywhere, no pose). Pure.

    detections: [{label, score, box: (x0, y0, x1, y1) normalized}].
    """
    for det in detections:
        try:
            if str(det.get("label", "")).lower() != "cell phone":
                continue
            if float(det.get("score", 0.0)) < PHONE_SCORE:
                continue
            box = tuple(det["box"])
            if len(box) != 4:
                continue
        except (TypeError, ValueError, KeyError):
            continue
        if not wrists or _near_wrist(box, wrists):
            return True
    return False


def wrists_from(lm: dict[int, tuple[float, float]]) -> list[tuple[float, float]]:
    """Wrist points present in the landmarks. Pure."""
    out = []
    for idx in (LEFT_WRIST, RIGHT_WRIST):
        try:
            out.append((float(lm[idx][0]), float(lm[idx][1])))
        except (KeyError, TypeError, IndexError):
            continue
    return out


class SustainTracker:
    """Fires once per continuous active stretch. Pure (clock injected)."""

    def __init__(self, required_s: float) -> None:
        self.required_s = required_s
        self._since: float | None = None

    def update(self, active: bool, now: float) -> bool:
        """Feed one sample; True exactly when the stretch trips. Pure."""
        if not active:
            self._since = None
            return False
        if self._since is None:
            self._since = now
        if now - self._since >= self.required_s:
            self._since = None  # one alert per stretch; re-arm after a break
            return True
        return False


# --- stats ---------------------------------------------------------------


def _read_state() -> dict:
    try:
        data = json.loads(state_path().read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(data: dict) -> None:
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        tmp = state_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, state_path())
    except OSError:
        pass


def load_baseline() -> dict[str, float] | None:
    """Calibrated upright metrics, or None. Never raises."""
    base = _read_state().get("baseline")
    if not isinstance(base, dict):
        return None
    try:
        return {k: float(base[k]) for k in ("head_forward", "shoulder_slope", "nose_y")}
    except (KeyError, TypeError, ValueError):
        return None


def save_baseline(metrics: dict[str, float]) -> None:
    """Persist the upright baseline. Never raises."""
    try:
        state = _read_state()
        state["baseline"] = dict(metrics)
        _write_state(state)
    except Exception:
        pass


def bump_stat(name: str) -> None:
    """Increment a session counter. Never raises."""
    try:
        state = _read_state()
        state[name] = int(state.get(name, 0) or 0) + 1
        _write_state(state)
    except Exception:
        pass


def add_monitored(seconds: float) -> None:
    """Accumulate monitored time. Never raises."""
    try:
        state = _read_state()
        state["monitored_s"] = float(state.get("monitored_s", 0.0) or 0.0) + seconds
        _write_state(state)
    except Exception:
        pass


def status() -> dict:
    """Counters + baseline presence for voice reports. Never raises."""
    try:
        state = _read_state()
        return {
            "slouch_alerts": int(state.get("slouch_alerts", 0) or 0),
            "phone_alerts": int(state.get("phone_alerts", 0) or 0),
            "minutes_monitored": round(
                float(state.get("monitored_s", 0.0) or 0.0) / 60.0, 1
            ),
            "calibrated": load_baseline() is not None,
            "running": is_running(),
        }
    except Exception:
        return {
            "slouch_alerts": 0,
            "phone_alerts": 0,
            "minutes_monitored": 0.0,
            "calibrated": False,
            "running": False,
        }


# --- mediapipe wrappers (fail-soft, injectable) ---------------------------


def _mp_image(rgb):
    import mediapipe as mp
    import numpy as np

    return mp.Image(
        image_format=mp.ImageFormat.SRGB,
        data=np.ascontiguousarray(rgb),
    )


def make_pose_landmarker(model_path: str | None = None):
    """PoseLandmarker (IMAGE mode), or None when unavailable."""
    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        path = model_path or str(models_dir() / "pose_landmarker_lite.task")
        if not Path(path).is_file():
            return None
        base = mp_python.BaseOptions(model_asset_path=path)
        opts = mp_vision.PoseLandmarkerOptions(
            base_options=base, running_mode=mp_vision.RunningMode.IMAGE
        )
        return mp_vision.PoseLandmarker.create_from_options(opts)
    except Exception:
        return None


def make_object_detector(model_path: str | None = None):
    """ObjectDetector (IMAGE mode), or None when unavailable."""
    try:
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        path = model_path or str(models_dir() / "efficientdet_lite0.tflite")
        if not Path(path).is_file():
            return None
        base = mp_python.BaseOptions(model_asset_path=path)
        opts = mp_vision.ObjectDetectorOptions(
            base_options=base,
            running_mode=mp_vision.RunningMode.IMAGE,
            score_threshold=PHONE_SCORE,
            max_results=5,
        )
        return mp_vision.ObjectDetector.create_from_options(opts)
    except Exception:
        return None


def extract_pose_points(result) -> dict[int, tuple[float, float]] | None:
    """First person's landmarks -> {index: (x, y)}. None when no pose."""
    try:
        poses = result.pose_landmarks
        if not poses:
            return None
        return {i: (lm.x, lm.y) for i, lm in enumerate(poses[0])}
    except (AttributeError, TypeError):
        return None


def extract_detections(result) -> list[dict]:
    """ObjectDetector result -> [{label, score, box}]. Never raises."""
    out: list[dict] = []
    try:
        for det in result.detections or []:
            cats = det.categories or []
            if not cats:
                continue
            best = max(cats, key=lambda c: c.score or 0.0)
            box = det.bounding_box
            out.append(
                {
                    "label": best.category_name or "",
                    "score": float(best.score or 0.0),
                    "box": (
                        float(box.origin_x or 0),
                        float(box.origin_y or 0),
                        float((box.origin_x or 0) + (box.width or 0)),
                        float((box.origin_y or 0) + (box.height or 0)),
                    ),
                }
            )
    except (AttributeError, TypeError, ValueError):
        pass
    # Bboxes arrive in pixels; callers normalise (see detect_once).
    return out


def _to_rgb(frame):
    import cv2

    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def detect_once(frame, pose=None, detector=None) -> tuple[dict | None, list[dict]]:
    """One frame -> (landmarks dict|None, detections). Fail-soft.

    Boxes are normalised to 0..1 using the frame size. Pass fakes for
    tests; real landmarker/detector objects are used otherwise.
    """
    lm: dict | None = None
    dets: list[dict] = []
    try:
        h, w = frame.shape[:2]
    except (AttributeError, TypeError):
        return None, []
    try:
        rgb = _to_rgb(frame)
        mp_image = _mp_image(rgb)
        if pose is not None:
            with contextlib.suppress(Exception):
                lm = extract_pose_points(pose.detect(mp_image))
        if detector is not None:
            with contextlib.suppress(Exception):
                raw = extract_detections(detector.detect(mp_image))
                dets = [
                    {
                        "label": d["label"],
                        "score": d["score"],
                        "box": (
                            d["box"][0] / w,
                            d["box"][1] / h,
                            d["box"][2] / w,
                            d["box"][3] / h,
                        ),
                    }
                    for d in raw
                ]
    except Exception:
        pass
    return lm, dets


def focus_active() -> bool:
    """A focus session running? False when focus.py is absent. Never raises."""
    try:
        import focus  # type: ignore

        return bool(focus.is_active())
    except Exception:
        return False


# --- calibration ----------------------------------------------------------


def calibrate(seconds: float = 3.0, frame=None, pose=None) -> dict | None:
    """Average Sir's upright metrics over ~3 s; persist as baseline.

    Pass a frame + pose fake in tests; otherwise snapshots the hub.
    Returns the baseline, or None when no pose was seen.
    """
    import camera_hub

    samples: list[dict] = []
    own_pose = False
    if pose is None:
        pose = make_pose_landmarker()
        own_pose = True
    try:
        deadline = time.time() + max(1.0, seconds)
        if frame is not None:
            # Single injected frame (tests): measure once, no hub needed.
            lm, _ = detect_once(frame, pose=pose)
            m = posture_metrics(lm) if lm else None
            if m:
                samples.append(m)
        else:
            while time.time() < deadline:
                snap = camera_hub.snapshot("eyes-calibrate", timeout=4.0)
                if snap is None:
                    time.sleep(0.4)
                    continue
                lm, _ = detect_once(snap, pose=pose)
                m = posture_metrics(lm) if lm else None
                if m:
                    samples.append(m)
                time.sleep(0.4)
    finally:
        if own_pose and pose is not None:
            with contextlib.suppress(Exception):
                pose.close()
    baseline = average_metrics(samples)
    if baseline:
        save_baseline(baseline)
    return baseline


# --- monitor --------------------------------------------------------------


class EyesMonitor:
    """2-fps posture+phone watch; inject everything for tests."""

    def __init__(
        self,
        *,
        frame=None,
        pose=None,
        detector=None,
        speaker=None,
        now=None,
        slouch_s: float = SLOUCH_SUSTAIN_S,
        phone_s: float = PHONE_SUSTAIN_S,
        phone_focus_s: float = PHONE_SUSTAIN_FOCUS_S,
    ) -> None:
        self._frame = frame  # () -> BGR array | None
        self._pose = pose
        self._detector = detector
        self._speak = speaker or (
            lambda text, source: __import__("speak").speak(
                text, source=source, min_gap_s=300
            )
        )
        self._now = now or time.time
        self._slouch = SustainTracker(slouch_s)
        self._phone = SustainTracker(phone_s)
        self._phone_focus_s = phone_focus_s
        self._slouch_n = 0
        self._phone_n = 0

    def poll(self, baseline: dict | None = None) -> None:
        """One frame: update sustain trackers, speak on trip. Never raises."""
        try:
            if baseline is None:
                baseline = load_baseline()
            snap = self._frame() if self._frame else None
            if snap is None:
                import camera_hub

                snap = camera_hub.latest_frame()
            if snap is None:
                return
            lm, dets = detect_once(snap, pose=self._pose, detector=self._detector)
            now = self._now()
            if lm and baseline:
                m = posture_metrics(lm)
                if m and self._slouch.update(slouching(m, baseline), now):
                    line = SLOUCH_LINES[self._slouch_n % len(SLOUCH_LINES)]
                    self._slouch_n += 1
                    with contextlib.suppress(Exception):
                        self._speak(line, "posture")
                    bump_stat("slouch_alerts")
            wrists = wrists_from(lm) if lm else []
            required = self._phone_focus_s if focus_active() else self._phone.required_s
            self._phone.required_s = required
            if self._phone.update(phone_in_hand(dets, wrists), now):
                line = PHONE_LINES[self._phone_n % len(PHONE_LINES)]
                self._phone_n += 1
                with contextlib.suppress(Exception):
                    self._speak(line, "phone")
                bump_stat("phone_alerts")
        except Exception:
            pass


_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop: threading.Event | None = None


def is_running() -> bool:
    """Posture watch active?"""
    with _lock:
        return _thread is not None and _thread.is_alive()


def start() -> bool:
    """Begin the 2-fps watch (holds a hub lease). False if already running."""
    import camera_hub

    global _thread, _stop
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False
        stop = threading.Event()
        baseline = load_baseline()

        def _loop() -> None:
            pose = make_pose_landmarker()
            detector = make_object_detector()
            mon = EyesMonitor(pose=pose, detector=detector)
            t0 = time.time()
            try:
                while not stop.is_set():
                    try:
                        camera_hub.request("eyes", 30.0)
                        mon.poll(baseline)
                    except Exception:
                        pass
                    stop.wait(1.0 / FPS)
            finally:
                with contextlib.suppress(Exception):
                    add_monitored(time.time() - t0)
                camera_hub.release("eyes")
                for closer in (pose, detector):
                    with contextlib.suppress(Exception):
                        if closer is not None:
                            closer.close()

        _stop = stop
        _thread = threading.Thread(target=_loop, name="eyes", daemon=True)
        _thread.start()
        return True


def stop() -> None:
    """End the watch. Never raises."""
    global _thread, _stop
    with _lock:
        stop, _stop = _stop, None
        thread, _thread = _thread, None
    if stop is not None:
        with contextlib.suppress(Exception):
            stop.set()
    if thread is not None:
        with contextlib.suppress(Exception):
            thread.join(timeout=3.0)


# --- appearance feedback (Gemini vision, free tier) ------------------------


def _parse_keys_file(path: Path) -> dict:
    out: dict = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            name, sep, value = line.partition("=")
            if not sep or not name.strip():
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            out[name.strip()] = value
    except OSError:
        pass
    return out


def google_key() -> str:
    """GOOGLE_API_KEY from env, else $JARVIS_HOME/keys.env. Never logs it."""
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if key:
        return key
    return (
        _parse_keys_file(jarvis_home() / "keys.env").get("GOOGLE_API_KEY", "").strip()
    )


def gemini_model() -> str:
    """Vision model: bridge's text-model env, default 3.8-flash. Pure (env)."""
    return os.environ.get("JARVIS_TEXT_MODEL", "").strip() or "gemini-3.8-flash"


def gemini_fallbacks() -> tuple[str, ...]:
    """Backup model IDs when the primary is over quota."""
    return ("gemini-3.7-flash",)


def downscale_jpeg(frame, max_side: int = 768) -> bytes:
    """BGR frame -> JPEG bytes capped at max_side px. Pure-ish (no I/O)."""
    import cv2

    h, w = frame.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise ValueError("jpeg encode failed")
    return bytes(buf)


def build_gemini_body(jpeg: bytes, question: str, model: str) -> tuple[str, bytes]:
    """(url, json body) for generateContent with one inline image. Pure."""
    prompt = (
        "You are Jarvis, a kind but honest British butler. Look at this "
        "photo of Sir at his desk and give brief, specific feedback on "
        "outfit, grooming, lighting and framing — 2 to 4 sentences, warm "
        "and direct, one concrete suggestion at most."
    )
    question = " ".join((question or "").split())
    if question:
        prompt += f" Sir asks: {question[:300]} Answer that first, then the feedback."
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )
    body = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": base64.b64encode(jpeg).decode(),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"maxOutputTokens": 300},
        }
    ).encode()
    return url, body


def parse_gemini_text(payload: dict) -> str:
    """First text part of a generateContent reply, '' when absent. Pure."""
    try:
        for part in payload["candidates"][0]["content"]["parts"]:
            text = part.get("text", "")
            if text and text.strip():
                return " ".join(text.split())
    except (KeyError, TypeError, IndexError, AttributeError):
        pass
    return ""


def _gemini_post(
    url: str, body: bytes, key: str, opener=None, timeout: float = 30.0
) -> dict:
    req = urllib.request.Request(
        f"{url}?key={key}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    open_fn = opener or urllib.request.urlopen
    with open_fn(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def describe_appearance(question: str = "", opener=None) -> str:
    """Look at Sir through the hub camera and comment, butler-style.

    Fail-soft: every failure returns a short speakable error, never raises.
    """
    try:
        import camera_hub

        frame = camera_hub.snapshot("eyes-look", timeout=4.0)
    except Exception:
        return "I couldn't open the camera just now, Sir."
    if frame is None:
        return "I couldn't see you just now, Sir — the camera gave no frame."
    try:
        jpeg = downscale_jpeg(frame)
    except Exception:
        return "I couldn't read the camera frame, Sir."
    key = google_key()
    if not key:
        return "My eyes need a GOOGLE_API_KEY in keys.env first, Sir."
    models = [gemini_model(), *gemini_fallbacks()]
    seen, last_err = set(), ""
    for model in [m for m in models if not (m in seen or seen.add(m))]:
        url, body = build_gemini_body(jpeg, question, model)
        try:
            text = parse_gemini_text(_gemini_post(url, body, key, opener=opener))
        except Exception as exc:
            last_err = str(exc)[:120]
            continue
        if text:
            return text[:1200]
        last_err = "empty reply"
    return f"My eyes blurred just now, Sir ({last_err or 'no reply'})."
