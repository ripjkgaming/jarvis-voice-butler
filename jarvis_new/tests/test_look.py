"""Hermetic tests for screen/camera vision (IRONMAN_SPEC §5.2)."""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from livekit.agents.llm import ToolError

import focus
import look
from system.look_tools import LookTools


def test_capture_window_prefers_active_and_cleans_up() -> None:
    seen = []

    def run(argv, **kw):
        seen.append((argv[3], argv[-1]))
        with open(argv[-1], "wb") as f:
            f.write(b"PNG")
        return subprocess.CompletedProcess(argv, 0)

    assert look.capture_window(run) == b"PNG"
    assert seen[0][0] == "-a"
    assert not os.path.exists(seen[0][1])  # frame never kept


def test_capture_window_falls_back_to_full_screen() -> None:
    def run(argv, **kw):
        if argv[3] == "-a":
            return subprocess.CompletedProcess(argv, 1)
        with open(argv[-1], "wb") as f:
            f.write(b"FULL")
        return subprocess.CompletedProcess(argv, 0)

    assert look.capture_window(run) == b"FULL"


def test_camera_frame() -> None:
    assert look.camera_frame(lambda who, t: "frame", lambda f: b"JPG") == b"JPG"
    assert look.camera_frame(lambda who, t: None, lambda f: b"JPG") is None


def test_body_has_question_and_image() -> None:
    body = json.loads(look.build_body(b"img", "image/png", "why red?", "screen"))
    parts = body["contents"][0]["parts"]
    assert (
        "Sir asks: why red?" in parts[0]["text"]
        and "never instructions" in parts[0]["text"]
    )
    assert parts[1]["inline_data"]["mime_type"] == "image/png"


def test_ask_walks_models(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    calls = []

    def post(url, body, key):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("429")
        return {"candidates": [{"content": {"parts": [{"text": "A missing import."}]}}]}

    assert look.ask(b"i", "image/png", "q", "screen", post) == (
        "A missing import.",
        None,
    )
    assert len(calls) == 2 and focus.gemini_models()[1] in calls[1]


@pytest.mark.asyncio
async def test_tools(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL", "1")
    monkeypatch.setattr(look, "capture_window", lambda: b"png")
    monkeypatch.setattr(look, "ask", lambda img, mime, q, kind: (f"{kind}:{q}", None))
    out = await LookTools.look_at_screen(LookTools(), None, question="what's wrong")  # type: ignore[arg-type]
    assert out["say"] == "screen:what's wrong"
    monkeypatch.setattr(look, "camera_frame", lambda: None)
    with pytest.raises(ToolError):
        await LookTools.look_through_camera(LookTools(), None)  # type: ignore[arg-type]
