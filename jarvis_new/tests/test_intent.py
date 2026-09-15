from intent.resolver import normalize, resolve_intent


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
