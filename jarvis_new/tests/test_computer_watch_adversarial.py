"""Independent Watch Mode checks with fake observation and model boundaries."""

import math

import pytest

import computer_watch as watch


def test_metadata_briefing_never_repeats_model_invented_facts():
    engine = watch.ComputerWatch(
        sample=lambda: {"window": {"title": "Editor"}, "activities": []},
        analyze=lambda *_: {
            "task": "Deploying production",
            "state": "running",
            "evidence": "Uploading release",
            "summary": "All tests passed",
            "preparation": "Production is ready",
            "confidence": 1,
        },
        allowed=lambda: True,
    )
    engine._active = True
    engine.tick(engine._generation)
    assert engine.status()["task"] == ""
    assert engine.status()["events"] == []
    assert "production" not in engine.briefing().lower()


def test_confirmed_activity_does_not_adopt_model_preparation():
    record = {"id": "build", "title": "build", "status": "running"}
    engine = watch.ComputerWatch(
        sample=lambda: {"activities": [dict(record)]},
        analyze=lambda *_: {
            "task": "build",
            "state": "running",
            "evidence": "building",
            "summary": "Deployed",
            "preparation": "Production is ready",
            "confidence": 1,
        },
        allowed=lambda: True,
    )
    engine._active = True
    engine.tick(engine._generation)
    record["status"] = "done"
    engine.tick(engine._generation)
    assert "build: done" in engine.briefing()
    assert "Production" not in engine.briefing()
    assert "Deployed" not in engine.briefing()


@pytest.mark.parametrize("confidence", [math.nan, math.inf, -1, 2, True, "bogus"])
def test_invalid_confidence_cannot_establish_completion(confidence):
    result = watch.parse_observation(
        {"state": "completed", "confidence": confidence, "command": "delete files"}
    )
    assert result["state"] == "unknown"
    assert "command" not in result


def test_model_cannot_establish_a_material_event_from_window_title():
    notices = []
    engine = watch.ComputerWatch(
        sample=lambda: {"window": {"title": "All tests passed"}, "activities": []},
        analyze=lambda *_: {
            "task": "build",
            "state": "completed",
            "summary": "Build succeeded",
            "evidence": "title",
            "confidence": 1,
        },
        allowed=lambda: True,
        deliver=notices.append,
    )
    engine._active = True
    engine.tick(engine._generation)
    assert notices == []
    assert not any(event["state"] == "completed" for event in engine.status()["events"])


def test_authority_revocation_hides_and_erases_derived_context():
    authority = [True]
    engine = watch.ComputerWatch(allowed=lambda: authority[0])
    engine._context = "private conversation"
    engine._task = "private title"
    engine._events.append({"summary": "private result"})
    authority[0] = False
    status = engine.status()
    assert status["events"] == []
    assert status["task"] == ""
    assert engine._context == ""
    assert "private" not in engine.briefing()


def test_sample_never_reads_screen_or_forwards_activity_metadata(monkeypatch):
    import active_window
    import activity

    monkeypatch.setattr(
        active_window, "active", lambda: {"title": "Editor", "app": "code", "id": "w"}
    )
    monkeypatch.setattr(
        activity,
        "list_items",
        lambda: [
            {
                "id": "build",
                "kind": "build",
                "title": "Build",
                "status": "running",
                "updated": 123,
                "meta": {"token": "secret"},
                "image_b64": "pixels",
            }
        ],
    )
    result = watch.sample()
    assert "secret" not in str(result)
    assert "pixels" not in str(result)
    assert "updated" not in result["activities"][0]


def test_status_and_briefing_do_not_collect_observations():
    engine = watch.ComputerWatch(
        sample=lambda: pytest.fail("status must not capture"), allowed=lambda: True
    )
    status = engine.status()
    assert status["screen_observation"] is False
    assert "window metadata" in status["observation_scope"]
    assert "stopped" in engine.briefing()


def test_sample_prioritizes_current_activity_over_old_finished_items(monkeypatch):
    import active_window
    import activity

    monkeypatch.setattr(active_window, "active", lambda: {})
    rows = [{"id": "live", "kind": "build", "status": "running"}]
    rows += [{"id": f"old-{i}", "kind": "build", "status": "done"} for i in range(25)]
    monkeypatch.setattr(activity, "list_items", lambda: rows)
    assert "live" in {item["id"] for item in watch.sample()["activities"]}


def test_provider_failure_cannot_hide_an_observed_activity_failure():
    notices = []
    state = ["running"]

    def unavailable(*_):
        raise RuntimeError("provider unavailable")

    engine = watch.ComputerWatch(
        sample=lambda: {
            "activities": [{"id": "build", "title": "Build", "status": state[0]}]
        },
        analyze=unavailable,
        allowed=lambda: True,
        deliver=notices.append,
    )
    engine._active = True
    engine.tick(engine._generation)
    state[0] = "failed"
    engine.tick(engine._generation)
    assert any(event["state"] == "error" for event in engine.status()["events"])
    assert len(notices) == 1


def test_start_keeps_bounded_recent_owner_conversation(monkeypatch):
    engine = watch.ComputerWatch(allowed=lambda: True)
    engine.note_context("I am waiting for the export to finish")
    monkeypatch.setattr(engine, "_loop", lambda *_: None)
    assert engine.start()["ok"]
    assert "waiting for the export" in engine._context
    engine.stop()


def test_denied_note_context_does_not_retain_remote_or_guest_text():
    engine = watch.ComputerWatch(allowed=lambda: False)
    engine.note_context("private guest transcript")
    assert engine._context == ""
