"""Always Aware vision call: model fallback on per-model quota, and context."""

import json

import pytest

from aware import vision

CLIP = [{"label": "M1", "mp4": b"MP4", "active": True}]
OK = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {
                        "text": json.dumps(
                            {
                                "summary": "Fractions worksheet",
                                "activity": "writing",
                                "importance": 1,
                                "sensitive": False,
                                "opportunities": [],
                            }
                        )
                    }
                ]
            }
        }
    ]
}


def test_quota_on_primary_falls_back_to_next_model():
    seen = []

    def post(url, body, key):
        seen.append(url)
        if "m1" in url:
            raise RuntimeError("HTTP Error 429: Too Many Requests")
        return OK

    res = vision.ask(CLIP, {}, key="k", models=["m1", "m2"], post=post)
    assert res["model"] == "m2" and len(seen) == 2


def test_quota_on_every_model_is_a_quota_error():
    def post(url, body, key):
        raise RuntimeError("429 quota")

    with pytest.raises(vision.QuotaError):
        vision.ask(CLIP, {}, key="k", models=["m1", "m2"], post=post)


def test_mixed_quota_and_outage_is_an_outage():
    def post(url, body, key):
        raise RuntimeError("429 quota" if "m1" in url else "503 down")

    with pytest.raises(vision.VisionError) as err:
        vision.ask(CLIP, {}, key="k", models=["m1", "m2"], post=post)
    assert not isinstance(err.value, vision.QuotaError)


def test_context_lists_recent_ticks_oldest_first():
    text = vision.context_text(
        {"time": "Sat 19:52", "history": ["19:46 Q1-3 done", "19:51 stuck on Q4"]}
    )
    assert "Recent ticks, oldest first:\n  - 19:46 Q1-3 done\n  - 19:51 stuck" in text


def test_worksheet_help_is_a_known_kind_and_prompt_explains_it():
    assert "worksheet_help" in vision.KINDS
    assert "hint" in vision.PROMPT and "LibreOffice" in vision.PROMPT
