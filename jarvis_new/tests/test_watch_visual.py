import time

import pytest

from watch_visual import VisualObserver, WatchConsent


class Backend:
    def __init__(self):
        self.selected = ("window", "geometry")
        self.image = b"first-frame"
        self.captures = 0

    async def select(self):
        return self.selected

    async def capture(self, selected, check):
        check()
        self.captures += 1
        return self.image

    async def ocr(self, image, check):
        check()
        return "Build finished successfully"


def consent():
    return WatchConsent(expires_at=time.time() + 600, max_calls=2)


@pytest.mark.asyncio
async def test_valid_quote_does_not_validate_invented_summary_or_task():
    observer = VisualObserver(
        consent(),
        "trusted",
        backend=Backend(),
        authorized=lambda: True,
        infer=lambda *_: {
            "task": "Production deployment",
            "state": "completed",
            "evidence": "Build finished successfully",
            "summary": "All 500 tests passed and production was deployed",
            "preparation": "Production is healthy",
            "confidence": 1,
        },
    )
    result = await observer.observe()
    assert result["state"] == "completed"
    assert "Build finished successfully" in result["summary"]
    assert "production" not in str(result).lower()
    assert "500" not in str(result)
    assert result["preparation"] == ""


@pytest.mark.asyncio
async def test_rejected_visual_claim_clears_invented_task():
    observer = VisualObserver(
        consent(),
        "trusted",
        backend=Backend(),
        authorized=lambda: True,
        infer=lambda *_: {
            "task": "Deploying production",
            "state": "running",
            "evidence": "Uploading release",
            "summary": "Deploying",
            "confidence": 1,
        },
    )
    result = await observer.observe()
    assert result["state"] == "unknown"
    assert result["task"] == ""


@pytest.mark.asyncio
async def test_grounded_frame_and_unchanged_budget():
    backend = Backend()
    calls = []

    def infer(image, text, context, check):
        calls.append(image)
        return {
            "task": "build",
            "state": "completed",
            "evidence": "Build finished successfully",
            "summary": "Build finished",
            "confidence": 0.95,
        }

    observer = VisualObserver(
        consent(),
        "trusted-session",
        backend=backend,
        infer=infer,
        authorized=lambda: True,
        min_interval_s=0,
    )
    result = await observer.observe()
    assert result["state"] == "completed"
    assert result["source"] == "selected_window_visual"
    assert await observer.observe() is None
    assert len(calls) == 1
    assert observer.status()["calls"] == 1
    assert not any(isinstance(value, bytes) for value in vars(observer).values())


@pytest.mark.asyncio
async def test_unsupported_claim_has_no_terminal_state():
    observer = VisualObserver(
        consent(),
        "trusted-session",
        backend=Backend(),
        authorized=lambda: True,
        infer=lambda *_: {
            "task": "build",
            "state": "error",
            "evidence": "bank password 1234",
            "confidence": 1,
        },
        min_interval_s=0,
    )
    assert (await observer.observe())["state"] == "unknown"


@pytest.mark.asyncio
async def test_stop_during_capture_prevents_inference():
    backend = Backend()
    observer = VisualObserver(
        consent(),
        "trusted-session",
        backend=backend,
        authorized=lambda: True,
        infer=lambda *_: pytest.fail("upload after cancellation"),
        min_interval_s=0,
    )

    async def capture(selected, check):
        observer.revoke()
        return b"pixels"

    backend.capture = capture
    with pytest.raises(Exception, match="cancelled"):
        await observer.observe()


@pytest.mark.asyncio
async def test_enrollment_does_not_follow_switched_window():
    backend = Backend()
    observer = VisualObserver(
        consent(), "trusted", backend=backend, authorized=lambda: True
    )
    await observer.enroll()
    backend.selected = ("other-window", "geometry")
    with pytest.raises(Exception, match="window changed"):
        await observer.observe()
    assert backend.captures == 0


@pytest.mark.asyncio
async def test_capture_occlusion_failure_has_no_fallback():
    backend = Backend()

    async def blocked(selected, check):
        from screen_understanding import ScreenError

        raise ScreenError("Covering windows must close")

    backend.capture = blocked
    observer = VisualObserver(
        consent(),
        "trusted",
        backend=backend,
        authorized=lambda: True,
        infer=lambda *_: pytest.fail("model despite failed capture"),
    )
    with pytest.raises(Exception, match="Covering windows"):
        await observer.observe()
    assert observer.status()["calls"] == 0


@pytest.mark.asyncio
async def test_expiry_during_capture_prevents_upload():
    now = [1000.0]
    backend = Backend()

    async def expired(selected, check):
        now[0] = 1002.0
        return b"secret screen pixels"

    backend.capture = expired
    observer = VisualObserver(
        WatchConsent(expires_at=1001),
        "trusted",
        backend=backend,
        authorized=lambda: True,
        clock=lambda: now[0],
        infer=lambda *_: pytest.fail("late upload"),
    )
    with pytest.raises(Exception, match="expired"):
        await observer.observe()
    assert observer.status()["expired"]
    assert "secret" not in repr(observer.status())


@pytest.mark.asyncio
async def test_expiry_during_inference_discards_late_result():
    now = [1000.0]

    def infer(*_):
        now[0] = 1002.0
        return {
            "state": "completed",
            "evidence": "Build finished successfully",
            "confidence": 1,
        }

    observer = VisualObserver(
        WatchConsent(expires_at=1001),
        "trusted",
        backend=Backend(),
        authorized=lambda: True,
        clock=lambda: now[0],
        infer=infer,
    )
    with pytest.raises((Exception, __import__("asyncio").CancelledError)):
        await observer.observe()
    assert observer.status()["state"] == "stopped"


@pytest.mark.asyncio
async def test_budget_cannot_be_reused_after_stop():
    backend = Backend()
    observer = VisualObserver(
        WatchConsent(expires_at=time.time() + 60, max_calls=1),
        "trusted",
        backend=backend,
        authorized=lambda: True,
        min_interval_s=0,
        infer=lambda *_: {},
    )
    await observer.observe()
    backend.image = b"second-frame"
    with pytest.raises(Exception, match="budget exhausted"):
        await observer.observe()
    assert backend.captures == 1
    observer.revoke()
    with pytest.raises(Exception, match="cancelled"):
        await observer.observe()
    assert observer.status()["calls"] == 1


@pytest.mark.parametrize(
    "values",
    [
        {"expires_at": float("inf")},
        {"expires_at": float("nan")},
        {"expires_at": time.time() + 99999},
        {"expires_at": 0},
        {"max_calls": True},
        {"max_calls": 61},
        {"max_calls": 0},
        {"selected_window": False},
        {"provider": "unapproved"},
    ],
)
def test_invalid_consent(values):
    args = {"expires_at": time.time() + 60, **values}
    with pytest.raises(Exception, match="consent"):
        VisualObserver(WatchConsent(**args), "trusted")


@pytest.mark.asyncio
async def test_malformed_model_fails_without_content_leak():
    observer = VisualObserver(
        consent(),
        "trusted",
        backend=Backend(),
        authorized=lambda: True,
        infer=lambda *_: "private password malformed json",
    )
    with pytest.raises(Exception) as failure:
        await observer.observe()
    assert "private password" not in str(failure.value)
    assert "Build finished successfully" not in repr(observer.status())


def test_resize_bounds_are_verified_in_memory():
    import io

    from PIL import Image

    from watch_visual import MAX_UPLOAD, bounded_image

    source = io.BytesIO()
    Image.new("RGB", (3000, 2000), color="navy").save(source, format="PNG")
    result = bounded_image(source.getvalue())
    with Image.open(io.BytesIO(result)) as resized:
        assert max(resized.size) <= 1280
        assert resized.format == "JPEG"
    assert len(result) <= MAX_UPLOAD
    with pytest.raises(Exception, match="Invalid selected-window image"):
        bounded_image(b"not pixels")


def test_consent_loader_requires_private_file_and_schema(tmp_path):
    import json

    from watch_visual import load_watch_consent

    path = tmp_path / "watch-consent.json"
    payload = {
        "version": 1,
        "provider": "gemini",
        "selected_window": True,
        "expires_at": time.time() + 60,
        "max_calls": 3,
    }
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    assert load_watch_consent(str(path)).max_calls == 3
    path.chmod(0o644)
    with pytest.raises(Exception, match="private owner"):
        load_watch_consent(str(path))
    path.chmod(0o600)
    payload["provider"] = "other"
    path.write_text(json.dumps(payload))
    with pytest.raises(Exception, match="consent"):
        load_watch_consent(str(path))


@pytest.mark.asyncio
async def test_disclosed_target_mismatch_blocks_capture():
    backend = Backend()
    backend.selected = (("chosen-id", "app-class", 123), "focus", "geometry")
    observer = VisualObserver(
        consent(), "trusted", backend=backend, authorized=lambda: True
    )
    with pytest.raises(Exception, match="Disclosed selected window changed"):
        await observer.enroll(expected_target=("other-id", "app-class", 123))
    assert backend.captures == 0
    assert observer.status()["state"] == "stopped"


@pytest.mark.asyncio
async def test_memory_workspace_ocr_cleans_success_failure_and_cancel(tmp_path):
    import asyncio
    from pathlib import Path

    from screen_understanding import ForegroundBackend, ScreenError

    for failure in (None, ScreenError("unavailable"), asyncio.CancelledError()):

        async def run(args, timeout, failure=failure):
            path = Path(args[1])
            assert path.parent.parent == tmp_path
            assert path.read_bytes() == b"private image"
            assert path.stat().st_mode & 0o777 == 0o600
            if failure is not None:
                raise failure
            return b"bounded local text"

        backend = ForegroundBackend(run=run, workspace_root=tmp_path)
        if failure is None:
            assert (
                await backend.ocr(b"private image", lambda: None)
                == "bounded local text"
            )
        else:
            with pytest.raises(type(failure)):
                await backend.ocr(b"private image", lambda: None)
        assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_memory_workspace_capture_cleanup_on_cancel(tmp_path):
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace

    from screen_understanding import ForegroundBackend

    snap = SimpleNamespace(signature=("owned",), frame=(0, 0, 80, 60), overlays=())
    closed = []

    class Probe:
        def __init__(self, *args):
            pass

        async def snapshot(self):
            return snap

        async def close(self):
            closed.append(True)

    async def run(args, timeout):
        path = Path(args[-1])
        assert path.parent.parent == tmp_path
        path.write_bytes(b"private owned pixels")
        raise asyncio.CancelledError()

    backend = ForegroundBackend(probe_factory=Probe, run=run, workspace_root=tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await backend.capture(snap.signature, lambda: None)
    assert closed == [True]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_exact_visible_quote_cannot_turn_running_into_completed():
    backend = Backend()

    async def ocr(image, check):
        return "Build running"

    backend.ocr = ocr
    observer = VisualObserver(
        consent(),
        "trusted",
        backend=backend,
        authorized=lambda: True,
        infer=lambda *_: {
            "task": "build",
            "state": "completed",
            "evidence": "Build running",
            "summary": "Everything passed",
            "confidence": 1,
        },
    )
    result = await observer.observe()
    assert result["state"] == "unknown"
    assert result["summary"] == "No grounded screen conclusion yet."
    assert observer._future is None


@pytest.mark.parametrize(
    "state,quote",
    [
        ("completed", "Build not completed"),
        ("completed", "Task failed"),
        ("error", "0 errors"),
        ("error", "no failures"),
    ],
)
def test_terminal_quote_requires_supporting_vocabulary(state, quote):
    from watch_visual import grounded_state

    assert not grounded_state(state, quote)


def test_configured_provider_is_toolless_bounded_and_closed(monkeypatch):
    import io
    from types import SimpleNamespace

    from google import genai
    from PIL import Image

    from watch_visual import infer_gemini

    sent = []
    closed = []
    checks = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["http_options"].timeout == 20000
            assert kwargs["http_options"].retry_options.attempts == 1
            self.models = self

        def generate_content(self, **kwargs):
            sent.append(kwargs)
            assert kwargs["config"].tools is None
            assert kwargs["config"].automatic_function_calling.disable is True
            assert kwargs["contents"][0].inline_data.mime_type == "image/jpeg"
            return SimpleNamespace(text='{"state":"unknown"}')

        def close(self):
            closed.append(True)

    monkeypatch.setenv("GOOGLE_API_KEY", "synthetic-key")
    monkeypatch.setattr(genai, "Client", Client)
    frame = io.BytesIO()
    Image.new("RGB", (80, 60), "white").save(frame, format="PNG")
    assert (
        infer_gemini(
            frame.getvalue(),
            "synthetic OCR",
            "synthetic task",
            lambda: checks.append(True),
        )
        == '{"state":"unknown"}'
    )
    assert len(sent) == 1
    assert len(checks) == 3
    assert closed == [True]


@pytest.mark.asyncio
async def test_native_capture_requires_fresh_target_and_distinct_grant():
    observer = VisualObserver(
        WatchConsent(expires_at=time.time() + 60, capture_policy="pinned_window"),
        "fresh-native-session",
        backend=Backend(),
        authorized=lambda: True,
    )
    with pytest.raises(Exception, match="Fresh pinned-window disclosure"):
        await observer.enroll()
    assert observer.status()["capture_policy"] == "pinned_window"
    assert observer.status()["calls"] == 0


def test_old_consent_file_never_grants_native_background_capture(tmp_path):
    import json

    from watch_visual import load_watch_consent

    path = tmp_path / "visual-grant.json"
    payload = {
        "version": 1,
        "provider": "gemini",
        "selected_window": True,
        "expires_at": time.time() + 60,
        "max_calls": 1,
    }
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    assert load_watch_consent(str(path)).capture_policy == "foreground_window"
    payload["capture_policy"] = "pinned_window"
    path.write_text(json.dumps(payload))
    with pytest.raises(Exception, match="Invalid visual watch consent"):
        load_watch_consent(str(path))
    payload["version"] = 2
    path.write_text(json.dumps(payload))
    assert load_watch_consent(str(path)).capture_policy == "pinned_window"
