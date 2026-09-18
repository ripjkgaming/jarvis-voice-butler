from prompts import (
    AGENT_INSTRUCTIONS,
    RESEARCH_INSTRUCTIONS,
    SYSTEM_INSTRUCTIONS,
)


def test_named_websites_are_opened_directly() -> None:
    assert "Autonomy is the default" in AGENT_INSTRUCTIONS
    assert "Do not ask which site or for the URL" in AGENT_INSTRUCTIONS
    assert "Only use search_the_web when no website" in AGENT_INSTRUCTIONS


def test_autonomous_chaining_without_prompting() -> None:
    assert "Chain browser tools autonomously" in AGENT_INSTRUCTIONS
    assert (
        "Read-only steps (open, read, inspect, scroll, search, navigating) never need confirmation"
        in AGENT_INSTRUCTIONS
    )
    assert "Use the browser tools proactively" in AGENT_INSTRUCTIONS


def test_no_step_by_step_confirmation_gate() -> None:
    assert "confirm completion before continuing" not in AGENT_INSTRUCTIONS
    assert "Collect required inputs first" not in AGENT_INSTRUCTIONS
    assert "ask the user for it before searching" not in AGENT_INSTRUCTIONS.lower()


def test_multistep_tasks_must_finish_before_speaking() -> None:
    assert "Never stop after the opening step" in AGENT_INSTRUCTIONS


def test_shorts_recipe_is_direct() -> None:
    assert "youtube shorts" in AGENT_INSTRUCTIONS
    assert "youtube.com/shorts" in AGENT_INSTRUCTIONS


def test_consent_dialogs_are_dismissed_not_blockers() -> None:
    assert "consent" in AGENT_INSTRUCTIONS.lower()


def test_sustained_scrolling_is_authorized() -> None:
    assert "auto_scroll" in AGENT_INSTRUCTIONS
    assert "Never claim you cannot automate scrolling" in AGENT_INSTRUCTIONS
    assert "Stop only when the user says stop" in AGENT_INSTRUCTIONS


def test_valet_persona_is_dry_not_dark() -> None:
    assert "proper English valet" in AGENT_INSTRUCTIONS
    assert "deadpan and understated" in AGENT_INSTRUCTIONS
    assert "dark humor" not in AGENT_INSTRUCTIONS
    assert "deeply sarcastic" not in AGENT_INSTRUCTIONS


def test_valet_persona_traits() -> None:
    text = AGENT_INSTRUCTIONS
    assert "not sycophantic" in text
    assert "concern first, quip second" in text
    assert "critical fact first" in text
    assert "Affectionate ribbing only" in text
    assert "never insubordinate" in text
    assert "As you wish" in text
    assert "at most one wry aside" in text


def test_specialists_share_valet_voice() -> None:
    assert "same dry British-butler voice" in RESEARCH_INSTRUCTIONS
    assert "same dry British-butler voice" in SYSTEM_INSTRUCTIONS


def test_inbox_routing_is_direct_not_browser() -> None:
    assert "NEVER open the browser for these" in AGENT_INSTRUCTIONS
    assert "your OWN gmail/news/weather/reddit tools" in AGENT_INSTRUCTIONS
    assert "Gmail and Reddit are NOT on this list" in AGENT_INSTRUCTIONS


def test_deep_research_prefers_direct_tools() -> None:
    assert "your OWN start_deep_research" in AGENT_INSTRUCTIONS


def test_whatsie_means_whatsapp_desktop() -> None:
    assert '"whatsie" means the WhatSie WhatsApp desktop client' in AGENT_INSTRUCTIONS


def test_cursor_navigation_backs_up_open_app() -> None:
    assert "Cursor navigation is the fallback" in SYSTEM_INSTRUCTIONS
    assert 'saying "I can\'t" without trying both is a failure' in SYSTEM_INSTRUCTIONS


def test_app_launch_and_multistep_desktop_chain() -> None:
    assert "ALWAYS call open_app first, NEVER window_action" in SYSTEM_INSTRUCTIONS
    assert "desktop_screenshot to view and read the resulting output" in SYSTEM_INSTRUCTIONS
    assert "ONLY THEN call transfer_back_to_main" in SYSTEM_INSTRUCTIONS


