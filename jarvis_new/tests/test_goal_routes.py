"""Read goals always reach their tool (intent/goals.py), however phrased."""

import pytest

import claim_guard
from intent import fast_path, goals

SUBJECTS = {"physics", "economics", "biology", "hindi"}


def _route(text):
    return goals.route(fast_path.clean(text), SUBJECTS)


@pytest.mark.parametrize(
    ("text", "tool", "args"),
    [
        ("What's my current email?", "gmail_inbox", {"n": 5, "unread_only": False}),
        ("Hey Jarvis, any new emails?", "gmail_inbox", {"n": 5, "unread_only": True}),
        ("check my inbox", "gmail_inbox", {"n": 5, "unread_only": False}),
        ("could you have a look at my mail for me", "gmail_inbox", {"n": 5, "unread_only": False}),
        ("read my latest email", "gmail_read", {"ref": "latest"}),
        ("When is my next exam?", "exam_times", {"when": "next"}),
        ("what exams do I have tomorrow", "exam_times", {"when": "tomorrow"}),
        ("when's my physics paper", "exam_schedule", {"action": "find", "subject": "physics"}),
        ("what's on my calendar today", "calendar_upcoming", {"days": 1}),
        ("is it going to rain today", "weather_now", {}),
        ("what's the weather like", "weather_now", {}),
        ("give me the news about formula one", "news_digest", {"topic": "formula one"}),
    ],
)
def test_read_goals_route_to_their_tool(text, tool, args) -> None:
    assert _route(text) == (tool, args)


@pytest.mark.parametrize(
    "text",
    [
        "send an email to my teacher",
        "reply to the latest email",
        "which emails did you reply to",
        "add my physics exam on Monday",
        "check my email and then open spotify",
        "play some music",
        "Os Music",
        "",
    ],
)
def test_writes_compounds_and_chatter_go_to_gemini(text) -> None:
    assert _route(text) is None


def test_routes_can_be_disabled(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_GOAL_ROUTES", "0")
    assert not goals.enabled()


@pytest.mark.asyncio
async def test_execute_runs_on_the_owner_that_has_the_tool() -> None:
    class Inbox:
        async def gmail_inbox(self, context, n=5, unread_only=False):
            return {"say": f"Latest mail: {n} {unread_only}"}

    class Other:
        pass

    ok, say = await goals.execute([Other(), Inbox()], "gmail_inbox", {"n": 3, "unread_only": True})
    assert ok and say == "Latest mail: 3 True"
    ok, say = await goals.execute([Other()], "gmail_inbox", {})
    assert not ok


def test_refusal_without_a_tool_call_is_caught() -> None:
    guard = claim_guard.ClaimGuard(clock=lambda: 0.0)
    guard.note_user("What's my current email?")
    verdict = guard.check("I am afraid I cannot access your emails at the moment, Sir.")
    assert verdict is not None and verdict.category == "refusal"
    assert "call it now" in claim_guard.reprompt(verdict, guard.user_text)
    assert claim_guard.fixed_line(verdict) == ""


def test_refusal_after_a_failed_tool_is_honest() -> None:
    guard = claim_guard.ClaimGuard(clock=lambda: 0.0)
    guard.note_user("What's my current email?")
    guard.note_tool("gmail_inbox", False)
    assert guard.check("I cannot access your emails, Sir.") is None
