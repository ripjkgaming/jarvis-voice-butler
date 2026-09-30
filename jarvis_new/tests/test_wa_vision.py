"""Hermetic tests for WhatsApp photo understanding (src/wa_vision.py)."""

from __future__ import annotations

import base64
import json

import pytest

import wa_autoreply
import wa_mimic
import wa_vision
from system import whatsapp

JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8fakejpeg").decode()
NOW = 1_759_000_000.0


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    monkeypatch.setenv("JARVIS_WA_VISION", "1")
    for var in ("JARVIS_WA_VISION_MODELS", "JARVIS_WA_MIMIC", "JARVIS_WA_BLOCK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(wa_mimic, "DEFAULT_MIMIC", ())
    return tmp_path


class Resp:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_pick_vision_models() -> None:
    data = [
        {
            "id": "a/text:free",
            "context_length": 900000,
            "architecture": {"input_modalities": ["text"]},
        },
        {
            "id": "b/vis:free",
            "context_length": 100,
            "architecture": {"input_modalities": ["text", "image"]},
        },
        {
            "id": "c/vis-big:free",
            "context_length": 5000,
            "architecture": {"input_modalities": ["image", "text"]},
        },
        {
            "id": "d/paid",
            "context_length": 9,
            "architecture": {"input_modalities": ["image"]},
        },
    ]
    assert wa_vision.pick_vision_models(data) == ["c/vis-big:free", "b/vis:free"]


def test_vision_models_discover_cache_and_pin(home, monkeypatch) -> None:
    hits = []

    def opener(req, timeout=None):
        hits.append(req.full_url)
        return Resp(
            {
                "data": [
                    {"id": "x/v:free", "architecture": {"input_modalities": ["image"]}}
                ]
            }
        )

    assert wa_vision.vision_models(opener, now=100.0) == ["x/v:free"]
    assert wa_vision.vision_models(opener, now=200.0) == ["x/v:free"]
    assert len(hits) == 1  # cached for a day

    def broken(req, timeout=None):
        raise OSError("offline")

    assert wa_vision.vision_models(broken, now=100.0 + 2 * 86400) == list(
        wa_vision.GUESS_MODELS
    )
    monkeypatch.setenv("JARVIS_WA_VISION_MODELS", "m1:free, m2:free")
    assert wa_vision.vision_models(broken) == ["m1:free", "m2:free"]


def test_describe_openrouter_sends_image_and_walks_chain(home, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    sent = []

    def opener(req, timeout=None):
        body = json.loads(req.data)
        sent.append(body)
        if body["model"] == "first:free":
            raise OSError("429")
        return Resp(
            {
                "choices": [
                    {"message": {"content": "<think>x</think> A cat on a laptop."}}
                ]
            }
        )

    text, warn = wa_vision.describe_openrouter(
        JPEG, "lol", opener, ["first:free", "second:free"]
    )
    assert (text, warn) == ("A cat on a laptop.", None)
    parts = sent[-1]["messages"][1]["content"]
    assert parts[1] == {"type": "image_url", "image_url": {"url": JPEG}}
    assert "caption: lol" in parts[0]["text"]


def test_describe_haiku_stages_file_and_cleans_up(home, monkeypatch) -> None:
    import claude_cli

    monkeypatch.setenv("JARVIS_CLAUDE", "1")
    seen = {}

    def runner(argv, **kw):
        import re
        import subprocess
        from pathlib import Path

        path = Path(re.search(r"file (\S+\.jpg)", kw["input"]).group(1))
        seen["bytes"] = path.read_bytes()
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "A meme about physics.", "")

    monkeypatch.setattr(claude_cli.shutil, "which", lambda n: "/usr/bin/claude")
    text, warn = wa_vision.describe_haiku(JPEG, runner=runner)
    assert text == "A meme about physics." and warn is None
    assert seen["bytes"] == b"\xff\xd8fakejpeg"
    assert "claude-haiku-4-5" in seen["argv"] and "Read" in seen["argv"]
    assert not list(claude_cli.claude_cwd().glob("wa-photo-*"))


def test_describe_falls_back_and_caches(home) -> None:
    calls = []

    def free(url, cap):
        calls.append("free")
        return "", "all rate limited"

    def haiku(url, cap):
        calls.append("haiku")
        return "A selfie.", None

    cache: dict = {}
    assert wa_vision.describe(JPEG, "", cache, free, haiku) == "A selfie."
    assert wa_vision.describe(JPEG, "", cache, free, haiku) == "A selfie."
    assert calls == ["free", "haiku"]


def test_describe_off_switch(home, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_WA_VISION", "0")
    assert wa_vision.describe(JPEG, "", {}, lambda *a: ("x", None)) == ""


def test_fill_photos_newest_first_and_strips_data() -> None:
    msgs = [
        {"text": "[photo]", "image": JPEG, "caption": ""},
        {"text": "hi"},
        {"text": "[photo] look", "image": JPEG + "A", "caption": "look"},
    ]
    n = wa_vision.fill_photos(
        msgs, {}, limit=1, describe_fn=lambda u, c, cache: "a pfp"
    )
    assert n == 1
    assert msgs[2]["text"] == "[photo: a pfp] look"
    assert msgs[0]["text"] == "[photo]"
    assert all("image" not in m for m in msgs)


def test_parse_messages_keeps_photos() -> None:
    rows = [
        {"pre": "[5:05 pm, 30/09/2026] Raphael Chan: ", "text": "", "img": JPEG},
        {
            "pre": "[5:05 pm, 30/09/2026] Raphael Chan: ",
            "text": "this one",
            "img": JPEG,
        },
        {"pre": "[5:06 pm, 30/09/2026] Raphael Chan: ", "text": ""},
    ]
    out = whatsapp.parse_messages(rows)
    assert [m["text"] for m in out] == ["[photo]", "[photo] this one"]
    assert out[1]["caption"] == "this one" and out[1]["image"] == JPEG


def test_attach_images_by_index() -> None:
    rows = [{}, {}, {}]
    whatsapp.attach_images(
        rows,
        [{"i": 2, "img": JPEG}, {"i": 9, "img": JPEG}, {"i": 0, "img": "javascript:x"}],
    )
    assert rows == [{}, {}, {"img": JPEG}]


def test_img_js_formats() -> None:
    js = whatsapp._IMG_JS % (16, 3)
    assert "slice(-16)" in js and "budget = 3" in js and "toDataURL" in js


def test_mimic_lookup_matches_full_chat_name(home) -> None:
    wa_mimic.add("Raphael")
    assert wa_mimic.lookup("Raphael Chan") == ""
    assert wa_mimic.lookup("raphael  chan") == ""
    assert wa_mimic.lookup("Raphaela") is None
    assert wa_mimic.lookup("Big Raphael") is None
    assert wa_mimic.lookup("Raphael C") == ""
    wa_mimic.add("Raphael Chan", "the real one")
    assert wa_mimic.lookup("Raphael Chan") == "the real one"  # exact wins
    assert wa_mimic.lookup("Raphael Chan Wei") == "the real one"  # longest wins


@pytest.mark.asyncio
async def test_reply_sees_photo_description(home, monkeypatch) -> None:
    import notify

    monkeypatch.setattr(notify, "send", lambda *a, **k: {"route": "test"})
    wa_mimic.add("Raphael")
    msgs = [
        {"text": "u shld send it", "me": False, "sender": "Raphael Chan"},
        {
            "text": "[photo]",
            "image": JPEG,
            "caption": "",
            "me": False,
            "sender": "Raphael Chan",
        },
    ]

    class WA:
        def __init__(self):
            self.sends: list = []

        async def read_chat(self, name, n):
            return {
                "ok": True,
                "header": "Raphael Chan\ncontact info",
                "messages": msgs,
            }

        async def send_chat(self, name, text):
            self.sends.append((name, text))
            return {"ok": True}

    prompts = []

    def mimic(prompt):
        prompts.append(prompt)
        return {
            "reply": "LMAO bro thats peak",
            "rude": False,
            "disengage": False,
            "handoff": False,
            "for_owner": "sent a meme",
        }, None

    wa = WA()
    out = await wa_autoreply.handle_chat(
        {"name": "Raphael Chan"},
        wa,
        {"chats": {}},
        NOW,
        True,
        lambda p: (None, "unused"),
        mimic,
        lambda *a: (None, "off"),
        lambda url, cap, cache: "an April Fools profile picture of a cat in a suit",
    )
    assert out == "sent"
    assert "[photo: an April Fools profile picture of a cat in a suit]" in prompts[0]
    assert "base64" not in prompts[0]
    assert wa.sends == [("Raphael Chan", "LMAO bro thats peak")]
