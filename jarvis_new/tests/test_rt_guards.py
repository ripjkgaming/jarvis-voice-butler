import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jarvis_rt.guards import RepeatGuard, is_foreign_speech


def test_foreign_script_flagged() -> None:
    assert is_foreign_speech("안녕하세요 감사합니다")
    assert is_foreign_speech("今天天气很好")
    assert is_foreign_speech("привет как дела")


def test_english_and_accents_not_flagged() -> None:
    assert not is_foreign_speech("open my calendar")
    assert not is_foreign_speech("café au lait please")
    assert not is_foreign_speech("")
    assert not is_foreign_speech("42 !?")


def test_within_turn_repeat_detected() -> None:
    g = RepeatGuard()
    g.start_turn()
    assert not g.feed("Very good, Sir. The calendar is open now. ")
    assert g.feed("The calendar is open now. ")


def test_cross_turn_repeat_only_without_user_text() -> None:
    g = RepeatGuard()
    g.start_turn()
    g.feed("Your battery is at eighty percent. ")
    g.end_turn()
    g.start_turn()
    assert g.feed("Your battery is at eighty percent. ", user_text="")
    g.start_turn()
    assert not g.feed("Your battery is at eighty percent. ", user_text="say that again")


def test_short_sentences_never_flagged() -> None:
    g = RepeatGuard()
    g.start_turn()
    assert not g.feed("Yes, Sir. ")
    assert not g.feed("Yes, Sir. ")


def test_cross_turn_expires() -> None:
    now = [1000.0]
    g = RepeatGuard(clock=lambda: now[0], window=60)
    g.start_turn()
    g.feed("Your battery is at eighty percent. ")
    g.end_turn()
    now[0] += 120
    g.start_turn()
    assert not g.feed("Your battery is at eighty percent. ", user_text="")
