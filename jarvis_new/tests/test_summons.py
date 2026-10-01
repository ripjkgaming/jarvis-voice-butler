def test_is_summons_gates_ack() -> None:
    from agent import _is_summons

    assert _is_summons("Jarvis?")
    assert _is_summons("hey Jarvis")
    assert _is_summons("ok Jarvis thanks")
    assert not _is_summons("Jarvis, what time is it")
    assert not _is_summons("can you check my calendar for tomorrow Jarvis please")
    assert not _is_summons("what time is it")
    assert not _is_summons("")


def test_wake_join_detected_by_summoner_identity() -> None:
    from types import SimpleNamespace

    from agent import _wake_summoned

    wake_room = SimpleNamespace(
        remote_participants={"a": SimpleNamespace(identity="jarvis-master")}
    )
    assert _wake_summoned(wake_room) is True
    app_room = SimpleNamespace(
        remote_participants={"u": SimpleNamespace(identity="user")}
    )
    assert _wake_summoned(app_room) is False
    assert _wake_summoned(SimpleNamespace()) is False
    assert _wake_summoned(None) is False


def test_join_decision_matrix() -> None:
    from agent import _join_decision

    # Wake summons always greet: the words were spent on the detector.
    assert _join_decision(True, "present") == "greet"
    assert _join_decision(True, "absent") == "greet"
    assert _join_decision(True, "unknown") == "greet"
    # Camera gate otherwise.
    assert _join_decision(False, "present") == "greet"
    assert _join_decision(False, "absent") == "remark"
    assert _join_decision(False, "unknown") == "silent"


def test_name_called_tolerates_stt_mangling() -> None:
    from agent import _name_called

    assert _name_called("Jarvis")
    assert _name_called("Jeeves, what time is it?")
    assert _name_called("hey Jeeves")
    assert _name_called("Jarvis, check my calendar")
    assert not _name_called("what time is it")
    assert not _name_called("the service is bad")
    assert not _name_called("jars of honey")
    assert not _name_called("")


def test_summons_includes_mangled_names() -> None:
    from agent import _is_summons

    assert _is_summons("Jeeves?")
    assert not _is_summons("Jeeves, what time is it please tell me now")


def test_name_aliases_cover_heard_manglings() -> None:
    from agent import _name_called

    for heard in ("jeeves", "jarves", "jervis", "JEEVES?"):
        assert _name_called(heard), heard
    for ambient in ("service", "jars", "harvest", "nervous"):
        assert not _name_called(ambient), ambient


def test_wake_reason_reads_the_wake_clients_tag():
    from types import SimpleNamespace

    from agent import DADDY_GREETING, _wake_reason

    def room(*parts):
        return SimpleNamespace(remote_participants={p.identity: p for p in parts})

    master = SimpleNamespace(identity="jarvis-master", attributes={"jarvis.wake": "daddy"})
    plain = SimpleNamespace(identity="jarvis-master", attributes={})
    user = SimpleNamespace(identity="user", attributes={"jarvis.wake": "daddy"})
    assert _wake_reason(room(master)) == "daddy"
    assert _wake_reason(room(plain)) == "wake"
    assert _wake_reason(room(user)) == ""
    assert _wake_reason(SimpleNamespace()) == ""
    assert DADDY_GREETING == "Welcome back, Sir."
    bare = SimpleNamespace(identity="jarvis-master", attributes={"jarvis.wake": "school-bare"})
    assert _wake_reason(room(bare)) == "school-bare"


def test_summon_token_carries_the_reason():
    import jwt as _jwt  # noqa: F401  (pyjwt ships with livekit-api)

    from wake_client import mint_summon_token

    tok = mint_summon_token(
        url="ws://x", api_key="k", api_secret="s" * 32, room="r", agent_name="a",
        reason="daddy",
    )
    import base64
    import json

    body = json.loads(base64.urlsafe_b64decode(tok.split(".")[1] + "=="))
    assert body["attributes"] == {"jarvis.wake": "daddy"}


def test_briefing_instructions_forbid_tools_and_keep_facts():
    from agent import _briefing_instructions

    text = _briefing_instructions("Auto: +19°C top stories: A; B. School: none")
    assert "do not call any tools" in text and "+19°C" in text and "headline" in text
