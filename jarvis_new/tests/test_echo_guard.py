from echo_guard import EchoGuard


def test_echo_of_recent_agent_speech_is_caught() -> None:
    g = EchoGuard()
    g.note_agent("You have three new messages, Sir, and one is from the school.", 100.0)
    assert g.is_echo("three new messages and one is from the school", 103.0)


def test_real_user_speech_is_not_echo() -> None:
    g = EchoGuard()
    g.note_agent("You have three new messages, Sir.", 100.0)
    assert not g.is_echo("add the school event to my calendar", 102.0)


def test_old_speech_and_short_fragments_ignored() -> None:
    g = EchoGuard()
    g.note_agent("You have three new messages, Sir.", 100.0)
    assert not g.is_echo("three new messages", 200.0)  # outside the window
    assert not g.is_echo("new messages", 101.0)  # under 3 words
    assert not EchoGuard().is_echo("three new messages", 101.0)  # nothing said
