"""Ling via OpenRouter chat fallback: fake opener, no network."""

import io
import json
import sys
import urllib.error

sys.path.insert(0, "src")

import openrouter_chat


class _Resp:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode()

    def read(self):
        return self._raw

    def close(self):
        pass


def _ok(content):
    return _Resp({"choices": [{"message": {"content": content}}]})


def _opener(payload_content, seen, content=_ok):
    def open_fn(req, timeout=None):
        seen["req"] = req
        seen["timeout"] = timeout
        return _ok(payload_content) if content is _ok else content

    return open_fn


def _headers(req):
    return {k.lower(): v for k, v in req.header_items()}


def test_success_posts_openrouter_shape(monkeypatch):
    monkeypatch.setattr(openrouter_chat, "api_key", lambda **k: "test-key")
    seen = {}
    reply, warning = openrouter_chat.chat_reply(
        "hello", system="You are Jarvis.", opener=_opener("At once, Sir.", seen)
    )
    assert (reply, warning) == ("At once, Sir.", None)
    req = seen["req"]
    assert req.full_url == "https://openrouter.ai/api/v1/chat/completions"
    assert req.get_method() == "POST"
    headers = _headers(req)
    assert headers["authorization"] == "Bearer test-key"
    assert headers["content-type"] == "application/json"
    assert headers["http-referer"] == "https://github.com/jarvis-voice-butler"
    assert headers["x-title"] == "Jarvis"
    body = json.loads(req.data.decode())
    assert body["model"] == openrouter_chat.MODEL
    assert body["messages"] == [
        {"role": "system", "content": "You are Jarvis."},
        {"role": "user", "content": "hello"},
    ]
    assert seen["timeout"] == 45.0


def test_default_model_is_free_ling():
    assert openrouter_chat.MODEL == "inclusionai/ling-3.0-flash-sante:free"


def test_think_blocks_stripped(monkeypatch):
    monkeypatch.setattr(openrouter_chat, "api_key", lambda **k: "test-key")
    seen = {}
    reply, warning = openrouter_chat.chat_reply(
        "hi",
        system="s",
        opener=_opener("<think>planning…</think>  Very good, Sir. ", seen),
    )
    assert (reply, warning) == ("Very good, Sir.", None)


def test_429_maps_to_rate_limited(monkeypatch):
    monkeypatch.setattr(openrouter_chat, "api_key", lambda **k: "test-key")

    def open_fn(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b"{}")
        )

    reply, warning = openrouter_chat.chat_reply("hi", system="s", opener=open_fn)
    assert reply == "" and "rate limited" in (warning or "")


def test_http_error_includes_status(monkeypatch):
    monkeypatch.setattr(openrouter_chat, "api_key", lambda **k: "test-key")

    def open_fn(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 500, "Internal Error", {}, io.BytesIO(b"{}")
        )

    reply, warning = openrouter_chat.chat_reply("hi", system="s", opener=open_fn)
    assert reply == "" and "500" in (warning or "")


def test_no_key_never_calls_opener(monkeypatch):
    monkeypatch.setattr(openrouter_chat, "api_key", lambda **k: "")

    def open_fn(req, timeout=None):
        raise AssertionError("no network without a key")

    reply, warning = openrouter_chat.chat_reply("hi", system="s", opener=open_fn)
    assert reply == "" and "key" in (warning or "").lower()


def test_timeout_is_warning(monkeypatch):
    monkeypatch.setattr(openrouter_chat, "api_key", lambda **k: "test-key")

    def open_fn(req, timeout=None):
        raise TimeoutError("timed out")

    reply, warning = openrouter_chat.chat_reply("hi", system="s", opener=open_fn)
    assert reply == "" and "timed out" in (warning or "")


def test_bad_json_is_warning(monkeypatch):
    monkeypatch.setattr(openrouter_chat, "api_key", lambda **k: "test-key")

    class _Bad:
        def read(self):
            return b"not json"

        def close(self):
            pass

    reply, warning = openrouter_chat.chat_reply(
        "hi", system="s", opener=lambda req, timeout=None: _Bad()
    )
    assert reply == "" and warning


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("JARVIS_CHAT_FALLBACK", "0")

    def open_fn(req, timeout=None):
        raise AssertionError("disabled fallback must not call the network")

    assert openrouter_chat.chat_reply("hi", system="s", opener=open_fn) == (
        "",
        "chat fallback disabled",
    )


def test_key_lookup_order_env_then_keys_file_then_auth(tmp_path):
    keys = tmp_path / "keys.env"
    keys.write_text('export OPENROUTER_API_KEY="file-key"\n')
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"openrouter": {"key": "auth-key"}}))
    env = {"OPENROUTER_API_KEY": "env-key"}
    assert (
        openrouter_chat.api_key(keys_path=keys, auth_path=auth, environ=env)
        == "env-key"
    )
    assert (
        openrouter_chat.api_key(keys_path=keys, auth_path=auth, environ={})
        == "file-key"
    )
    keys.write_text("# no key here\nOTHER=1\n")
    assert (
        openrouter_chat.api_key(keys_path=keys, auth_path=auth, environ={})
        == "auth-key"
    )
    auth.write_text("{}")
    assert openrouter_chat.api_key(keys_path=keys, auth_path=auth, environ={}) == ""
