import ast
from pathlib import Path
from types import SimpleNamespace

import phone_calls


def helper():
    source = ast.parse((Path(__file__).parents[1] / "src/agent.py").read_text())
    f = next(
        n
        for n in source.body
        if isinstance(n, ast.FunctionDef) and n.name == "_wake_announce"
    )
    ns = {}
    exec(compile(ast.Module(body=[f], type_ignores=[]), "announce", "exec"), ns)  # noqa: S102 - checked-in helper only
    return ns["_wake_announce"]


def test_manual_desktop_and_phone_announcement_are_distinct(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    read = helper()

    def room(identity, attrs):
        return SimpleNamespace(
            remote_participants={
                "x": SimpleNamespace(identity=identity, attributes=attrs)
            }
        )

    assert read(room("normal-user", {})) == ""
    assert (
        read(room("jarvis-master", {"jarvis.announce": "Existing desktop opener"}))
        == "Existing desktop opener"
    )
    seen = []
    monkeypatch.setattr(
        phone_calls,
        "consume_announcement",
        lambda call_id, identity: (
            seen.append((call_id, identity)) or "Server-owned reminder"
        ),
    )
    assert (
        read(
            room(
                "jarvis-phone-verified",
                {
                    "jarvis.phone_call": "verified",
                    "jarvis.announce": "Untrusted injected title",
                },
            )
        )
        == "Server-owned reminder"
    )
    assert seen == [("verified", "jarvis-phone-verified")]
