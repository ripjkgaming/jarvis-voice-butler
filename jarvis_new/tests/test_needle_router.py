"""Needle 2 routing layer: mapping, gating, and resolver integration.

The real ``cactus-needle`` package is never required here: a fake
module is injected into sys.modules, so these run offline on every
machine. Live verification against the real model was done once by
hand (crisp phrasing routes at 0.8+ confidence, paraphrase misses
abstain low instead of misfiring).
"""

import sys
import types

import pytest

from intent import needle_router
from intent.resolver import resolve_intent


def _fake_needle_module(complete_fn):
    mod = types.ModuleType("needle")

    def tool(fn=None, **kwargs):
        if fn is None:
            return lambda f: f
        return fn

    mod.tool = tool

    class Needle:
        def __init__(self, tools=None, weights=None):
            self.tools = tools
            self.weights = weights

        def complete(self, text):
            return complete_fn(text)

    mod.Needle = Needle
    return mod


@pytest.fixture
def needle_env(monkeypatch):
    monkeypatch.setenv("JARVIS_NEEDLE", "1")
    yield
    needle_router.reset_agent()
    needle_router.reset_agent()


def test_disabled_by_env_flag(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_NEEDLE", "0")
    assert needle_router.needle_enabled() is False
    assert needle_router.route_with_needle("what time is it") is None


def test_disabled_when_package_missing(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_NEEDLE", "1")
    monkeypatch.delitem(sys.modules, "needle", raising=False)
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "needle":
            raise ImportError("no needle here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert needle_router.needle_enabled() is False
    assert needle_router.route_with_needle("what time is it") is None


def test_to_result_maps_call(needle_env, monkeypatch) -> None:
    raw = {
        "type": "call",
        "function_calls": [{"name": "tell_time", "arguments": {}}],
        "confidence": 0.986,
    }
    out = needle_router._to_result(raw)
    assert out == {"action": "tell_time", "params": {}, "confidence": 0.986}


def test_to_result_rewrites_website_to_url(needle_env) -> None:
    raw = {
        "type": "call",
        "function_calls": [{"name": "open_website", "arguments": {"site": "youtube"}}],
        "confidence": 0.9,
    }
    out = needle_router._to_result(raw)
    assert out["action"] == "open_url"
    assert out["params"] == {"url": "youtube"}


def test_to_result_rejects_abstains_and_unknowns(needle_env) -> None:
    assert needle_router._to_result({"type": "respond", "confidence": 0.9}) is None
    assert (
        needle_router._to_result(
            {
                "type": "call",
                "function_calls": [{"name": "self_destruct", "arguments": {}}],
                "confidence": 0.99,
            }
        )
        is None
    )
    assert needle_router._to_result({"type": "call", "confidence": 0.9}) is None


def test_to_result_clamps_confidence(needle_env) -> None:
    raw = {
        "type": "call",
        "function_calls": [{"name": "tell_time", "arguments": {}}],
        "confidence": 99.0,
    }
    assert needle_router._to_result(raw)["confidence"] == 1.0


def test_route_uses_agent_complete(needle_env, monkeypatch) -> None:
    fake = _fake_needle_module(
        lambda text: {
            "type": "call",
            "function_calls": [{"name": "open_app", "arguments": {"app": "whatsie"}}],
            "confidence": 0.7,
        }
    )
    monkeypatch.setitem(sys.modules, "needle", fake)
    out = needle_router.route_with_needle("open whatsie")
    assert out["action"] == "open_app"
    assert out["params"] == {"app": "whatsie"}
    assert out["confidence"] == pytest.approx(0.7)


def test_route_survives_engine_failure(needle_env, monkeypatch) -> None:
    def boom(text):
        raise RuntimeError("engine exploded")

    monkeypatch.setitem(sys.modules, "needle", _fake_needle_module(boom))
    assert needle_router.route_with_needle("hello") is None


def test_resolver_prefers_needle_over_weak_fuzzy(needle_env, monkeypatch) -> None:
    """A paraphrase fuzzy mangles but Needle nails: Needle wins."""
    fake = _fake_needle_module(
        lambda text: {
            "type": "call",
            "function_calls": [{"name": "tell_time", "arguments": {}}],
            "confidence": 0.95,
        }
    )
    monkeypatch.setitem(sys.modules, "needle", fake)
    result = resolve_intent("kindly tell me the hour good sir")
    assert result.action == "tell_time"
    assert result.should_act


def test_resolver_low_confidence_needle_still_asks(needle_env, monkeypatch) -> None:
    fake = _fake_needle_module(lambda text: {"type": "respond", "confidence": 0.1})
    monkeypatch.setitem(sys.modules, "needle", fake)
    result = resolve_intent("xqvw blorpt zzz")
    assert result.action == "unknown"
    assert result.clarification


def test_resolver_needle_confirm_tier(needle_env, monkeypatch) -> None:
    fake = _fake_needle_module(
        lambda text: {
            "type": "call",
            "function_calls": [{"name": "open_app", "arguments": {"app": "whatsie"}}],
            "confidence": 0.65,
        }
    )
    monkeypatch.setitem(sys.modules, "needle", fake)
    result = resolve_intent("perhaps open the message thing")
    assert result.action == "open_app"
    assert result.should_confirm
    assert not result.should_act


def test_resolver_exact_still_wins_without_calling_needle(
    needle_env, monkeypatch
) -> None:
    calls: list[str] = []

    def spy(text):
        calls.append(text)
        return {"type": "respond", "confidence": 0.0}

    monkeypatch.setitem(sys.modules, "needle", _fake_needle_module(spy))
    result = resolve_intent("brief me")
    assert result.action == "morning_briefing"
    assert result.confidence == 1.0
    assert calls == []


def test_resolver_needle_exception_falls_back(needle_env, monkeypatch) -> None:
    def boom(text):
        raise RuntimeError("nope")

    monkeypatch.setitem(sys.modules, "needle", _fake_needle_module(boom))
    result = resolve_intent("hot rod redd")
    assert result.action == "set_color"
    assert result.confidence >= 0.5
