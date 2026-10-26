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


def test_short_answer_to_a_question_is_not_echo() -> None:
    g = EchoGuard()
    g.note_agent("Shall I open Spotify, Sir?", 100.0)
    assert not g.is_echo("yes open Spotify", 102.0)
    assert not g.is_echo("yes please open spotify", 102.0)
    assert not g.is_echo("yeah go ahead and open spotify", 102.0)


def test_four_word_echo_without_affirmation_still_caught() -> None:
    g = EchoGuard()
    g.note_agent("Shall I open Spotify for you, Sir?", 100.0)
    assert g.is_echo("shall I open Spotify", 102.0)
    assert not g.is_echo("open Spotify", 102.0)  # under 4 words


def test_short_exact_echo_only_when_agent_audio_active():
    guard = EchoGuard()
    guard.note_agent("You have three new messages, Sir.", 100)
    assert not guard.is_echo("three new messages", 101)
    guard.speaking = True
    assert guard.is_echo("three new messages", 101)
    assert not guard.is_echo("stop", 101)
    assert not guard.is_echo("yes three new messages", 101)


def test_reordered_vocabulary_is_not_echo():
    guard = EchoGuard()
    guard.note_agent("Please send Bob the message and read the news", 100)
    assert not guard.is_echo("read the message and send Bob the news please", 101)
