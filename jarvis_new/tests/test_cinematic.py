import json
from types import SimpleNamespace

from system import cinematic


def test_window_marker_precedes_action_and_is_exact(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == "busctl":
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"data": [{"pid": {"data": 123}, "caption": {"data": "Notes"}}]}
                ),
            )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(cinematic, "enabled", lambda: True)
    assert cinematic.mark_window("close", "{abc}", run=run)
    payload = json.loads(calls[-2][-1])
    assert payload["action"] == "close" and payload["pid"] == 123
    assert payload["caption"] == "Notes" and payload["expires"] > 0
    assert calls[-1][-2:] == ["org.kde.kwin.Effects.reconfigureEffect", "jarvis-energy"]


def test_marker_failure_never_blocks_action(monkeypatch):
    monkeypatch.setattr(cinematic, "enabled", lambda: True)

    def broken(*args, **kwargs):
        raise OSError("no compositor")

    assert not cinematic.mark_window("close", "{abc}", run=broken)


def test_launch_classes_are_specific():
    assert cinematic.launch_classes(["/usr/bin/dolphin"]) == ["dolphin"]
    assert cinematic.launch_classes(["flatpak", "run", "com.brave.Browser"]) == [
        "com.brave.browser",
        "brave-browser",
        "brave",
    ]
    assert cinematic.launch_classes(["sh", "-c", "something"]) == []


def test_unknown_actions_emit_nothing(monkeypatch):
    monkeypatch.setattr(cinematic, "enabled", lambda: True)
    assert not cinematic.mark_window(
        "delete-files", "x", run=lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )


def test_geometry_batch_only_marks_exact_identities(monkeypatch):
    monkeypatch.setattr(cinematic, "enabled", lambda: True)
    emitted = []
    monkeypatch.setattr(
        cinematic, "_emit", lambda action, **kw: emitted.append((action, kw)) or True
    )
    assert cinematic.mark_geometry([{"pid": 12, "caption": "Owned"}])
    assert emitted == [("geometry", {"windows": [{"pid": 12, "caption": "Owned"}]})]
    assert not cinematic.mark_geometry([{"pid": 0, "caption": ""}])
