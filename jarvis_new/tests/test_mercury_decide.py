"""Hermetic tests for src/mercury_decide.py and the urgency Mercury path."""

import io
import json
import urllib.error

import mercury_decide
import urgency_judge

MSG = {"sender": "Mum", "subject": "Call", "body": "Grandad fell, call asap"}


class _Resp(io.BytesIO):
    pass


def _opener(payload=None, code=None):
    def open_fn(req, timeout=None):
        if code:
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
        return _Resp(json.dumps(payload).encode())

    return open_fn


def _ans(urgent, scam):
    return {
        "answers": {
            "urgent": {"type": "noul", "noul": urgent},
            "scam": {"type": "noul", "noul": scam},
        }
    }


def _on(monkeypatch):
    monkeypatch.setenv("JARVIS_MERCURY", "1")
    monkeypatch.setattr(mercury_decide, "api_keys", lambda: ["k"])


def test_default_model_is_larger_perplexity(monkeypatch):
    _on(monkeypatch)
    assert mercury_decide.DEFAULT_MODEL == "perplexity/pplx-decider-v1.2"
    monkeypatch.setattr(mercury_decide, "MODEL", mercury_decide.DEFAULT_MODEL)
    seen = []

    def opener(req, timeout=None):
        seen.append(json.loads(req.data))
        return _Resp(json.dumps(_ans(0.9, 0.1)).encode())

    _, warning = mercury_decide.decide("email", {"urgent": {}}, opener=opener)
    assert warning is None
    assert seen[0]["model"] == "perplexity/pplx-decider-v1.2"


def test_dedicated_key_env_takes_priority(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mercury_decide.openrouter_chat, "_jarvis_home", lambda env: tmp_path
    )
    (tmp_path / "keys.env").write_text("JARVIS_DECIDE_API_KEY=file-key\n")
    monkeypatch.setattr(
        mercury_decide.openrouter_chat, "api_key", lambda: "general-key"
    )
    assert mercury_decide.api_keys(
        {
            "JARVIS_DECIDE_API_KEY": " dedicated-key ",
            "OPENROUTER_API_KEY_2": "secondary-key",
        }
    ) == ["dedicated-key"]


def test_dedicated_key_from_private_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mercury_decide.openrouter_chat, "_jarvis_home", lambda env: tmp_path
    )
    (tmp_path / "keys.env").write_text("JARVIS_DECIDE_API_KEY='file-key'\n")
    monkeypatch.setattr(
        mercury_decide.openrouter_chat, "api_key", lambda: "general-key"
    )
    assert mercury_decide.api_keys({}) == ["file-key"]


def test_legacy_keys_still_work_without_dedicated_key(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mercury_decide.openrouter_chat, "_jarvis_home", lambda env: tmp_path
    )
    monkeypatch.setattr(
        mercury_decide.openrouter_chat, "api_key", lambda: "general-key"
    )
    assert mercury_decide.api_keys({"OPENROUTER_API_KEY_2": "secondary-key"}) == [
        "general-key",
        "secondary-key",
    ]


def test_reason_identifies_selected_model(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(mercury_decide, "MODEL", "perplexity/pplx-decider-v1.2")
    monkeypatch.setattr(
        mercury_decide, "decide", lambda *a, **kw: (_ans(0.9, 0.1)["answers"], None)
    )
    verdict, warning = urgency_judge.judge(MSG)
    assert warning is None
    assert verdict["reason"].startswith("perplexity/pplx-decider-v1.2 ")


def test_decide_ok(monkeypatch):
    _on(monkeypatch)
    out, warn = mercury_decide.decide("s", {}, opener=_opener(_ans(0.9, 0.1)))
    assert warn is None and mercury_decide.yes_prob(out, "urgent") == 0.9


def test_decide_quota_and_disabled(monkeypatch):
    _on(monkeypatch)
    for code in (402, 429):
        out, warn = mercury_decide.decide("s", {}, opener=_opener(code=code))
        assert out is None and "quota spent" in warn
    monkeypatch.setenv("JARVIS_MERCURY", "0")
    assert mercury_decide.decide("s", {})[0] is None


def test_urgency_uses_mercury_then_falls_back(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(
        mercury_decide, "decide", lambda *a, **k: (_ans(0.99, 0.0)["answers"], None)
    )
    v, w = urgency_judge.judge(MSG)
    assert w is None and v["urgent"] and not v["scam"]
    monkeypatch.setattr(mercury_decide, "decide", lambda *a, **k: (None, "quota"))
    monkeypatch.setattr(
        urgency_judge.chatgpt_cli,
        "chatgpt_reply",
        lambda *a, **k: ('{"urgent": false, "scam": false, "reason": "fb"}', None),
    )
    v, w = urgency_judge.judge(MSG)
    assert v["reason"] == "fb"


def test_urgency_scam_overrides_urgent(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(
        mercury_decide, "decide", lambda *a, **k: (_ans(0.9, 0.95)["answers"], None)
    )
    v, _ = urgency_judge.judge(MSG)
    assert v["scam"] and not v["urgent"]


def test_two_keys_alternate_and_fail_over(monkeypatch):
    _on(monkeypatch)
    monkeypatch.setattr(mercury_decide, "api_keys", lambda: ["a", "b"])
    mercury_decide._rest_until.clear()
    used = []

    def opener(req, timeout=None):
        key = req.headers["Authorization"].split()[-1]
        used.append(key)
        if key == "a":
            raise urllib.error.HTTPError(req.full_url, 429, "x", {}, None)
        return _Resp(json.dumps(_ans(0.9, 0.1)).encode())

    for _ in range(3):
        out, warn = mercury_decide.decide("s", {}, opener=opener)
        assert warn is None and out
    assert used.count("a") == 1  # rested after its 429; b served the rest
    mercury_decide._rest_until.clear()


def test_event_gate_skips_chatgpt_only_when_confident(monkeypatch):
    import event_extractor as ee

    _on(monkeypatch)
    parsed = {"sender": "x", "subject": "Sale", "body": "50% off"}
    monkeypatch.setattr(
        mercury_decide,
        "decide",
        lambda *a, **k: ({"has_event": {"noul": 0.01}}, None),
    )
    assert ee.mercury_says_no_event(parsed)
    assert ee.extract(parsed) == (None, None)
    monkeypatch.setattr(
        mercury_decide,
        "decide",
        lambda *a, **k: ({"has_event": {"noul": 0.4}}, None),
    )
    assert not ee.mercury_says_no_event(parsed)
    monkeypatch.setattr(mercury_decide, "decide", lambda *a, **k: (None, "quota"))
    assert not ee.mercury_says_no_event(parsed)
