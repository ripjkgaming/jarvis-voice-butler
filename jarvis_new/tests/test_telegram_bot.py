"""Tests for the Telegram sidecar + setup (all fakes, no network)."""

import contextlib
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

sys.path.insert(0, "src")

import telegram_bot as tg
from telegram_bot import TelegramBot

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load_setup():
    spec = importlib.util.spec_from_file_location(
        "telegram_setup", SCRIPTS / "telegram_setup.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeNet:
    """One fake opener for the Bot API and the bridge. (req, timeout) -> bytes."""

    def __init__(self) -> None:
        self.all_updates: list[dict] = []
        self.sent: list[tuple[int, str]] = []  # (chat_id, text)
        self.voices: list[tuple[int, int]] = []  # (chat_id, ogg bytes len)
        self.actions: list[tuple[int, str]] = []
        self.chat_reply = "Very good, Sir."
        self.chat_seen: list[dict] = []
        self.status = {
            "ok": True,
            "version": "0.2.0",
            "uptime_s": 3700,
            "pipeline": "realtime",
            "livekit_configured": True,
        }
        self.mic_seen: list[dict] = []
        self.voice_bytes = b"OGGIN"
        self.calls = 0

    def _params(self, req) -> dict:
        raw = (req.data or b"").decode(errors="replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}

    def __call__(self, req, timeout=10.0) -> bytes:
        self.calls += 1
        url = req.full_url
        if url.endswith("/getUpdates"):
            params = self._params(req)
            off = params.get("offset")
            items = self.all_updates
            if off is not None:
                with contextlib.suppress(ValueError):
                    items = [u for u in items if u["update_id"] >= int(off)]
            return json.dumps({"ok": True, "result": items}).encode()
        if url.endswith("/sendMessage"):
            params = self._params(req)
            self.sent.append((int(params["chat_id"]), params.get("text", "")))
            return json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
        if url.endswith("/sendChatAction"):
            params = self._params(req)
            self.actions.append((int(params["chat_id"]), params.get("action", "")))
            return json.dumps({"ok": True, "result": True}).encode()
        if url.endswith("/sendVoice"):
            self.voices.append((0, len(req.data or b"")))
            return json.dumps({"ok": True, "result": {"message_id": 2}}).encode()
        if url.endswith("/getFile"):
            return json.dumps(
                {"ok": True, "result": {"file_path": "voice/note.ogg"}}
            ).encode()
        if "/file/bot" in url:
            return self.voice_bytes
        if url.endswith("/chat"):
            body = json.loads((req.data or b"{}").decode())
            self.chat_seen.append(body)
            return json.dumps({"ok": True, "reply": self.chat_reply}).encode()
        if url.endswith("/status"):
            return json.dumps(self.status).encode()
        if url.endswith("/mic"):
            body = json.loads((req.data or b"{}").decode())
            self.mic_seen.append(body)
            return json.dumps({"ok": True, "muted": body.get("muted")}).encode()
        raise AssertionError(f"unexpected url {url}")


def msg_update(uid: int, chat: int, text: str, user: int = 7) -> dict:
    return {
        "update_id": uid,
        "message": {
            "message_id": uid,
            "date": 1,
            "chat": {"id": chat},
            "from": {"id": user},
            "text": text,
        },
    }


def voice_update(uid: int, chat: int, file_id: str = "f1") -> dict:
    return {
        "update_id": uid,
        "message": {
            "message_id": uid,
            "date": 1,
            "chat": {"id": chat},
            "from": {"id": 7},
            "voice": {"file_id": file_id, "duration": 2},
        },
    }


def write_pair(home: Path, code: str, expires_at: float) -> None:
    (home / "telegram_pair.json").write_text(
        json.dumps({"code": code, "expires_at": expires_at})
    )


def make_bot(tmp_path: Path, net: FakeNet, **kwargs) -> TelegramBot:
    return TelegramBot(
        token="TESTTOKEN",
        home=tmp_path,
        opener=net,
        transcribe=lambda ogg: "hello jarvis",
        synth_wav=lambda text: b"WAVBYTES",
        run=lambda argv, payload, timeout: b"OGGBYTES",
        clock=lambda: 1_000_000.0,
        **kwargs,
    )


# --- pure helpers ------------------------------------------------------------


def test_parse_command() -> None:
    assert tg.parse_command("/voice on") == ("voice", "on")
    assert tg.parse_command("/start@MyBot 123456") == ("start", "123456")
    assert tg.parse_command("/help") == ("help", "")
    assert tg.parse_command("hello") == ("", "")


def test_extract_pair_code() -> None:
    assert tg.extract_pair_code("/start 123456") == "123456"
    assert tg.extract_pair_code("123456") == "123456"
    assert tg.extract_pair_code("/start") is None
    assert tg.extract_pair_code("hello") is None


def test_pairing_ok_and_expired() -> None:
    pair = {"code": "123456", "expires_at": 2000.0}
    assert tg.pairing_ok(pair, "123456", 1999.0) is True
    assert tg.pairing_ok(pair, "123456", 2000.0) is False  # expired
    assert tg.pairing_ok(pair, "000000", 1999.0) is False
    assert tg.pairing_ok(None, "123456", 1999.0) is False
    assert tg.pair_expired(pair, 2000.0) is True
    assert tg.pair_expired(pair, 1999.0) is False


def test_backoff_curve() -> None:
    assert tg.backoff_s(1) == 1.0
    assert tg.backoff_s(3) == 4.0
    assert tg.backoff_s(99) == tg.BACKOFF_CAP_S


def test_split_message() -> None:
    assert tg.split_message("hi") == ["hi"]
    parts = tg.split_message("x" * 8500)
    assert len(parts) == 3 and all(len(p) <= tg.MSG_LIMIT for p in parts)
    assert "".join(parts) == "x" * 8500


def test_multipart_round_trip() -> None:
    body, ctype = tg.build_multipart(
        {"chat_id": "7"},
        {"voice": ("reply.ogg", b"\x00\x01", "audio/ogg")},
        boundary="b",
    )
    assert ctype == "multipart/form-data; boundary=b"
    assert b'name="chat_id"\r\n\r\n7' in body
    assert b'filename="reply.ogg"' in body and body.endswith(b"--b--\r\n")


def test_format_status() -> None:
    line = tg.format_status(
        {
            "version": "0.2.0",
            "uptime_s": 3700,
            "pipeline": "realtime",
            "livekit_configured": True,
        }
    )
    assert "0.2.0" in line and "1h01" in line and "linked" in line
    assert tg.format_status(None) != ""


def test_keys_env_and_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    (tmp_path / "keys.env").write_text('OTHER=1\nTELEGRAM_BOT_TOKEN="abc"\n')
    assert tg.load_token(tmp_path) == "abc"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "envtok")
    assert tg.load_token(tmp_path) == "envtok"


# --- pairing -----------------------------------------------------------------


def test_pair_correct_code(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [msg_update(10, 111, "/start 123456")]
    write_pair(tmp_path, "123456", 2_000_000.0)
    bot = make_bot(tmp_path, net)
    assert bot.poll_once() == "ok"
    saved = json.loads((tmp_path / "telegram.json").read_text())
    assert saved["chat_id"] == 111 and saved["offset"] == 11
    assert not (tmp_path / "telegram_pair.json").exists()  # one-time
    assert any("Paired" in text for _, text in net.sent)


def test_pair_expired_code(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [msg_update(10, 111, "/start 123456")]
    write_pair(tmp_path, "123456", 999.0)  # long past the fake clock
    bot = make_bot(tmp_path, net)
    bot.poll_once()
    assert bot.state["chat_id"] is None
    assert any("expired" in text for _, text in net.sent)


def test_second_chat_refused_and_rate_limited(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [
        msg_update(10, 222, "hello"),
        msg_update(11, 222, "hello again"),
    ]
    bot = make_bot(tmp_path, net)
    bot.state.update({"chat_id": 111, "user_id": 7})
    bot.poll_once()
    refusals = [t for _, t in net.sent if "one master" in t]
    assert len(refusals) == 1  # second knock inside the gap: silence
    assert net.chat_seen == []  # strangers never reach the brain


def test_unpaired_text_asks_for_code(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [msg_update(10, 111, "hello?")]
    bot = make_bot(tmp_path, net)
    bot.poll_once()
    assert any("pairing code" in t for _, t in net.sent)
    assert net.chat_seen == []


# --- chat ---------------------------------------------------------------------


def test_text_flow_and_offset(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [msg_update(10, 111, "hi"), msg_update(11, 111, "hi2")]
    bot = make_bot(tmp_path, net)
    bot.state.update({"chat_id": 111, "user_id": 7})
    assert bot.poll_once() == "ok"
    assert bot.state["offset"] == 12
    assert (111, "typing") in [(c, a) for c, a in net.actions]
    assert [t for _, t in net.sent] == ["Very good, Sir."] * 2
    assert len(net.chat_seen) == 2
    assert net.chat_seen[1]["history"]  # second turn carries history


def test_long_reply_split(tmp_path: Path) -> None:
    net = FakeNet()
    net.chat_reply = "y" * 8500
    net.all_updates = [msg_update(10, 111, "talk a lot")]
    bot = make_bot(tmp_path, net)
    bot.state.update({"chat_id": 111, "user_id": 7})
    bot.poll_once()
    assert len(net.sent) == 3
    assert all(len(t) <= tg.MSG_LIMIT for _, t in net.sent)


def test_text_only_by_default(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [msg_update(10, 111, "hi")]
    bot = make_bot(tmp_path, net)
    bot.state.update({"chat_id": 111, "user_id": 7})
    bot.poll_once()
    assert net.voices == []


def test_voice_in_gets_text_plus_voice(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [voice_update(10, 111)]
    bot = make_bot(tmp_path, net)
    bot.state.update({"chat_id": 111, "user_id": 7})
    bot.poll_once()
    assert net.chat_seen and net.chat_seen[0]["text"] == "hello jarvis"
    assert [t for _, t in net.sent] == ["Very good, Sir."]
    assert len(net.voices) == 1  # the Piper voice note


def test_voice_toggle_persists(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [msg_update(10, 111, "/voice on"), msg_update(11, 111, "hi")]
    bot = make_bot(tmp_path, net)
    bot.state.update({"chat_id": 111, "user_id": 7})
    bot.poll_once()
    assert bot.state["voice"] is True
    assert len(net.voices) == 1  # text-in now also earns a voice note
    saved = json.loads((tmp_path / "telegram.json").read_text())
    assert saved["voice"] is True


def test_commands(tmp_path: Path) -> None:
    net = FakeNet()
    net.all_updates = [
        msg_update(10, 111, "/status"),
        msg_update(11, 111, "/mute"),
        msg_update(12, 111, "/unmute"),
        msg_update(13, 111, "/help"),
        msg_update(14, 111, "/bogus"),
    ]
    bot = make_bot(tmp_path, net)
    bot.state.update({"chat_id": 111, "user_id": 7})
    bot.poll_once()
    texts = [t for _, t in net.sent]
    assert "0.2.0" in texts[0]
    assert "muted" in texts[1] and "live" in texts[2]
    assert net.mic_seen == [{"muted": True}, {"muted": False}]
    assert "/status" in texts[3] and "Unknown command" in texts[4]


def test_no_token_idle() -> None:
    net = FakeNet()
    bot = TelegramBot(token="", home=None, opener=net)
    assert bot.poll_once() == "idle"
    assert net.calls == 0


def test_start_thread_no_token_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import time as _time

    monkeypatch.setattr(tg, "IDLE_S", 0.01)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    thread, stop = tg.start_thread()
    try:
        _time.sleep(0.05)
        assert thread.is_alive()
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


# --- setup script --------------------------------------------------------------


def test_upsert_env_line_preserves() -> None:
    setup = load_setup()
    out = setup.upsert_env_line(
        "A=1\nTELEGRAM_BOT_TOKEN=old\n# c\n", "TELEGRAM_BOT_TOKEN", "new"
    )
    assert "A=1" in out and "# c" in out and "TELEGRAM_BOT_TOKEN=new" in out
    assert out.count("TELEGRAM_BOT_TOKEN") == 1
    assert (
        setup.upsert_env_line("", "TELEGRAM_BOT_TOKEN", "x").strip()
        == "TELEGRAM_BOT_TOKEN=x"
    )


def test_validate_token_and_shape() -> None:
    setup = load_setup()
    assert setup.valid_token_shape("123:abcDEF_-xyz12345678901234567890123") is True
    assert setup.valid_token_shape("nope") is False

    def fake_opener(req, timeout):
        assert "getMe" in req.full_url
        return json.dumps({"ok": True, "result": {"username": "JarvisBot"}}).encode()

    assert setup.validate_token("123:abc", opener=fake_opener) == {
        "ok": True,
        "username": "JarvisBot",
    }

    def bad_opener(req, timeout):
        return json.dumps({"ok": False, "description": "Unauthorized"}).encode()

    assert setup.validate_token("123:abc", opener=bad_opener)["ok"] is False


def test_generate_code_shape() -> None:
    setup = load_setup()
    for _ in range(20):
        code = setup.generate_code()
        assert len(code) == 6 and code.isdigit()
