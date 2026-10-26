"""Watch Mode lifecycle, evidence and privacy regressions; no live desktop/model."""

import threading

import computer_watch as watch


def test_commands_are_anchored():
    assert watch.parse_command("Jarvis, watch the computer") == "start"
    assert watch.parse_command("stop watching the computer") == "stop"
    assert watch.parse_command("what happened while watching") == "briefing"
    assert watch.parse_command("don't watch the computer") is None
    assert watch.parse_command("guard the laptop") is None


def test_observation_validation_rejects_untrusted_actions():
    got = watch.parse_observation(
        '{"task":"build","state":"error","evidence":"exit 1","summary":"Build failed","preparation":"Check dependency","confidence":0.9,"command":"rm -rf"}'
    )
    assert got["state"] == "error"
    assert "command" not in got
    assert (
        watch.parse_observation('{"state":"completed","confidence":0.2}')["state"]
        == "unknown"
    )


def test_dedup_and_bounded_briefing():
    notices = []
    observations = [
        {"activities": [{"id": "build", "title": "build", "status": "running"}]}
    ]
    engine = watch.ComputerWatch(
        sample=lambda: observations[0],
        analyze=lambda s, c: {
            "task": "build",
            "state": "completed",
            "evidence": "All tests passed",
            "summary": "Build completed",
            "preparation": "Ready to review",
            "confidence": 0.9,
        },
        allowed=lambda: True,
        deliver=notices.append,
        poll_s=100,
    )
    engine._active = True
    engine.tick(engine._generation)
    observations[0] = {
        "activities": [
            {
                "id": "build",
                "title": "build",
                "status": "done",
                "detail": "All tests passed",
            }
        ]
    }
    for _ in range(50):
        engine.tick(engine._generation)
    assert len(notices) == 1
    assert len(engine.status()["events"]) == 1
    assert "build: done" in engine.briefing()
    assert "Ready to review" not in engine.briefing()


def test_stop_discards_late_model_result_and_refuses_overlap():
    entered, release = threading.Event(), threading.Event()

    def analyze(s, c):
        entered.set()
        release.wait(2)
        return {
            "task": "build",
            "state": "error",
            "evidence": "failed",
            "summary": "failed",
            "confidence": 1,
        }

    notices = []
    engine = watch.ComputerWatch(
        sample=lambda: {}, analyze=analyze, allowed=lambda: True, deliver=notices.append
    )
    assert engine.start()["ok"]
    assert entered.wait(1)
    engine.stop()
    assert not engine.start()["ok"]
    release.set()
    engine._thread.join(2)
    assert notices == []
    assert engine.status()["events"] == []


def test_guest_change_during_capture_suppresses_model_and_delivery():
    allowed = [True]

    def sample():
        allowed[0] = False
        return {}

    engine = watch.ComputerWatch(
        sample=sample,
        analyze=lambda s, c: (_ for _ in ()).throw(AssertionError("private analysis")),
        allowed=lambda: allowed[0],
    )
    engine._active = True
    engine.tick(engine._generation)
    assert not engine.status()["active"]


def test_failure_is_visible_without_fabricated_success():
    engine = watch.ComputerWatch(
        sample=lambda: (_ for _ in ()).throw(RuntimeError("capture")),
        allowed=lambda: True,
    )
    engine._active = True
    engine.tick(engine._generation)
    assert engine.status()["state"] == "degraded"
    assert "unavailable" in engine.briefing().lower()


def test_baseline_finished_and_disappeared_activity_are_not_new_completion():
    records = [[{"id": "build", "title": "build", "status": "done"}]]
    notices = []
    engine = watch.ComputerWatch(
        sample=lambda: {"activities": records[0]},
        analyze=lambda *_: {
            "task": "build",
            "state": "completed",
            "evidence": "guess",
            "confidence": 1,
        },
        allowed=lambda: True,
        deliver=notices.append,
    )
    engine._active = True
    engine.tick(engine._generation)
    records[0] = []
    engine.tick(engine._generation)
    assert notices == []
    assert engine.status()["events"] == []


def test_reused_activity_id_reports_each_confirmed_run_even_without_model():
    record = [{"id": "build", "title": "build", "status": "running"}]
    notices = []

    def unavailable(*_):
        raise RuntimeError("provider unavailable")

    engine = watch.ComputerWatch(
        sample=lambda: {"activities": [dict(record[0])]},
        analyze=unavailable,
        allowed=lambda: True,
        deliver=notices.append,
    )
    engine._active = True
    for state in ("running", "done", "running", "done"):
        record[0]["status"] = state
        engine.tick(engine._generation)
    assert len(notices) == 2
    assert len(engine.status()["events"]) == 2
    assert "done" in engine.briefing()
    assert engine.status()["state"] == "degraded"


def test_start_retains_bounded_recent_context_and_stop_erases_it():
    analyzed = []
    ready = threading.Event()

    def analyze(observation, context):
        analyzed.append(context)
        ready.set()
        return {"state": "unknown"}

    engine = watch.ComputerWatch(
        sample=lambda: {}, analyze=analyze, allowed=lambda: True, poll_s=100
    )
    engine.note_context("I am waiting for the compiler to finish.")
    engine.note_context("I am waiting for the compiler to finish.")
    engine.note_context("watch the computer")
    engine.start()
    assert ready.wait(1)
    engine.stop()
    engine._thread.join(1)
    assert analyzed == ["\nI am waiting for the compiler to finish."]
    assert engine._context == ""


def test_revoked_prestart_context_cannot_cross_owner_boundary():
    authority = [True]
    engine = watch.ComputerWatch(allowed=lambda: authority[0])
    engine.note_context("owner private task")
    authority[0] = False
    engine.note_context("guest question")
    authority[0] = True
    assert engine._context == ""


def test_default_notice_does_not_queue_or_speak_after_watch_stops(monkeypatch):
    import notify

    sent = []
    monkeypatch.setattr(notify, "send", lambda text, **kwargs: sent.append(kwargs))
    watch.deliver("A watched activity completed. Ask for the watch briefing.")
    assert sent[0]["allow_speech"] is False
    assert sent[0]["queue"] is False


def test_stopped_capture_does_not_erase_next_owner_context_or_saved_briefing():
    entered, release = threading.Event(), threading.Event()

    def sample():
        entered.set()
        release.wait(2)
        return {}

    engine = watch.ComputerWatch(sample=sample, allowed=lambda: True)
    engine.start()
    assert entered.wait(1)
    engine._events.append({"summary": "Previous activity completed", "preparation": ""})
    engine.stop()
    engine.note_context("Next I will compile the library.")
    release.set()
    engine._thread.join(1)
    assert "compile the library" in engine._context
    assert engine.status()["events"][0]["summary"] == "Previous activity completed"
