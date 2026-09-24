def test_is_summons_gates_ack() -> None:
    from agent import _is_summons

    assert _is_summons("Jarvis?")
    assert _is_summons("hey Jarvis")
    assert _is_summons("ok Jarvis thanks")
    assert not _is_summons("Jarvis, what time is it")
    assert not _is_summons("can you check my calendar for tomorrow Jarvis please")
    assert not _is_summons("what time is it")
    assert not _is_summons("")
