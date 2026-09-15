from prompts import (
    AGENT_INSTRUCTIONS,
    PERSONA_BASE,
    PERSONA_CRITICAL,
    PERSONA_FOCUSED,
    PUSHBACK_POLICY,
    URGENCY_TIERS,
)


def test_tiers_exist_and_differ() -> None:
    assert "wry aside" in PERSONA_BASE
    assert "wit off" in PERSONA_FOCUSED
    assert "critical fact comes first" in PERSONA_CRITICAL
    assert PERSONA_BASE != PERSONA_FOCUSED != PERSONA_CRITICAL


def test_pushback_names_risk_plus_alternative() -> None:
    assert "challenge bad decisions" in PUSHBACK_POLICY
    assert "alternative" in PUSHBACK_POLICY
    assert "Hard gates still refuse" in PUSHBACK_POLICY


def test_agent_instructions_carry_tiers() -> None:
    assert "PERSONA_FOCUSED" in AGENT_INSTRUCTIONS
    assert "PERSONA_CRITICAL" in AGENT_INSTRUCTIONS
    assert "PUSHBACK_POLICY" in AGENT_INSTRUCTIONS
    # Guardrail: tier text must not smuggle step-by-step confirmation gates.
    assert "Collect required inputs first" not in AGENT_INSTRUCTIONS
    assert "confirm completion before continuing" not in AGENT_INSTRUCTIONS
    assert URGENCY_TIERS.strip() in AGENT_INSTRUCTIONS
