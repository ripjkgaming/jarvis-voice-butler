import pytest

from intent import needle_router
from intent.resolver import keyword_route, normalize, resolve_intent


@pytest.fixture(autouse=True)
def needle_disabled(monkeypatch):
    """These tests pin layers 1-3: the stochastic model stays out."""
    monkeypatch.setenv("JARVIS_NEEDLE", "0")
    needle_router.reset_agent()
    yield
    needle_router.reset_agent()


def test_exact_alias_confidence_one() -> None:
    result = resolve_intent("Give me the hot rod red")
    assert result.action == "set_color"
    assert result.params == {"color": "crimson"}
    assert result.confidence == 1.0
    assert result.should_act


def test_wakeword_and_pleases_stripped() -> None:
    assert normalize("Jarvis, uh brief me please") == "brief me"


def test_fuzzy_match_confirms() -> None:
    result = resolve_intent("hot rod redd")
    assert result.action == "set_color"
    assert result.confidence >= 0.5
    assert result.clarification is not None


def test_sarcastic_fragment_still_resolves() -> None:
    result = resolve_intent("yeah, punch it, genius")
    assert result.action in ("max_throttle", "unknown")


def test_gibberish_asks_once() -> None:
    result = resolve_intent("xqvw blorpt zzz")
    assert result.action == "unknown"
    assert not result.should_act and not result.should_confirm
    assert result.clarification


def test_empty_asks_again() -> None:
    assert resolve_intent("   ").clarification is not None


def test_custom_aliases_merge() -> None:
    result = resolve_intent(
        "movie night", {"movie night": {"action": "dim_lights", "params": {}}}
    )
    assert result.action == "dim_lights"
    assert result.should_act


def test_pentest_seeds_resolve() -> None:
    assert resolve_intent("scan my box").action == "nmap_scan"
    assert resolve_intent("please freeze testing").action == "freeze_testing"
    assert resolve_intent("check my exposure").action == "pentest_report"


def test_gmail_never_routes_to_pentest() -> None:
    """Regression: "check my gmail inbox" matched "recon my box" (0.562)."""
    result = resolve_intent("check my gmail inbox please")
    assert result.action == "gmail_inbox"
    assert result.should_act


def test_canonical_time_and_volume_seeds() -> None:
    assert resolve_intent("what time is it").action == "tell_time"
    assert resolve_intent("crank it up").params == {"action": "up"}
    assert resolve_intent("turn it down").params == {"action": "down"}
    assert resolve_intent("mute it").params == {"action": "mute"}
    assert resolve_intent("unmute").params == {"action": "unmute"}


def test_keyword_layer_routes_app_opens_to_handoff() -> None:
    result = resolve_intent("open the files app")
    assert result.action == "transfer_to_system_control"
    assert result.params["app"] == "files"
    assert result.should_confirm and not result.should_act


def test_keyword_layer_routes_known_sites() -> None:
    result = resolve_intent("open youtube")
    assert result.action == "open_url"
    assert result.params == {"url": "youtube"}


def test_keyword_layer_extracts_volume_direction() -> None:
    assert resolve_intent("make it louder").params == {"action": "up"}
    assert resolve_intent("a bit quieter").params == {"action": "down"}


def test_keyword_layer_stays_in_confirm_tier() -> None:
    result = keyword_route("what is the weather in london")
    assert result is not None
    assert result.action == "weather_now"
    assert 0.5 <= result.confidence < 0.8


def test_keyword_route_returns_none_without_keywords() -> None:
    assert keyword_route("xqvw blorpt zzz") is None


def test_whatsapp_and_todo_keywords() -> None:
    assert resolve_intent("read my whatsapp").action == "whatsapp_read"
    assert resolve_intent("remind me to buy milk").action == "manage_todo"
