"""Visual coordinator tests use injected evidence; no desktop or provider access."""

import threading
import time

import computer_watch as watch


class Visual:
    def __init__(self, result=None):
        self.result = result
        self.revoked = False
        self.calls = 0

    async def enroll(self):
        pass

    async def observe(self, context, check):
        check()
        self.calls += 1
        return self.result

    def revoke(self):
        self.revoked = True

    def status(self):
        return {"calls": self.calls, "expired": False, "budget_exhausted": False}


def engine(visual, notices):
    instance = watch.ComputerWatch(
        sample=lambda: (_ for _ in ()).throw(AssertionError("metadata capture")),
        analyze=lambda *_: (_ for _ in ()).throw(AssertionError("metadata provider")),
        allowed=lambda: True,
        deliver=notices.append,
    )
    instance._active = True
    instance._visual = visual
    return instance


def observation(state):
    return {
        "task": "Build",
        "state": state,
        "evidence": "Visible " + state,
        "summary": "Build " + state,
        "preparation": "Review output",
        "confidence": 0.95,
        "command": "lock computer",
        "consent": True,
        "image": "secret raw pixels",
    }


def test_visual_baseline_never_reports_existing_completed_work():
    visual = Visual(observation("completed"))
    notices = []
    instance = engine(visual, notices)
    instance.tick(instance._generation)
    assert not notices
    assert not instance.status()["events"]


def test_visual_transition_input_needed_and_completion_dedup():
    visual = Visual(observation("running"))
    notices = []
    instance = engine(visual, notices)
    instance.tick(instance._generation)
    visual.result = observation("completed")
    instance.tick(instance._generation)
    instance.tick(instance._generation)
    visual.result = observation("input_needed")
    instance.tick(instance._generation)
    instance.tick(instance._generation)
    assert len(notices) == 2
    assert instance.status()["screen_observation"] is True
    assert "Review output" in instance.briefing()
    assert "raw pixels" not in str(instance.status())
    assert "command" not in str(instance.status())


def test_visual_stop_clears_private_derivations_and_revokes_capture():
    visual = Visual(observation("running"))
    instance = engine(visual, [])
    instance.tick(instance._generation)
    instance.note_context("private owner conversation")
    instance.stop()
    assert visual.revoked
    assert instance._context == ""
    assert instance.status()["events"] == []
    assert instance.status()["task"] == ""
    assert instance.status()["visual"] is None


def test_stop_during_visual_provider_discards_late_evidence():
    entered, release = threading.Event(), threading.Event()
    visual = Visual()

    async def observe(context, check):
        entered.set()
        release.wait(2)
        return observation("input_needed")

    visual.observe = observe
    notices = []
    instance = engine(visual, notices)
    worker = threading.Thread(target=instance.tick, args=(instance._generation,))
    worker.start()
    assert entered.wait(1)
    instance.stop()
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert not notices
    assert not instance.status()["events"]


def test_authority_loss_after_model_clears_visual_private_state():
    owner = [True]
    visual = Visual(observation("input_needed"))
    instance = engine(visual, [])
    instance._allowed = lambda: owner[0]

    async def observe(context, check):
        owner[0] = False
        return observation("input_needed")

    visual.observe = observe
    instance.tick(instance._generation)
    assert visual.revoked
    assert instance.status()["active"] is False
    assert not instance.status()["events"]


def test_failed_visual_enrollment_never_starts_worker(monkeypatch):
    import sys
    import types

    visual = Visual()

    async def denied():
        raise RuntimeError("private selected window unavailable")

    visual.enroll = denied
    monkeypatch.setitem(
        sys.modules, "watch_visual", types.SimpleNamespace(VisualObserver=Visual)
    )
    instance = watch.ComputerWatch(
        allowed=lambda: True, visual_factory=lambda *_a, **_k: visual
    )
    result = instance.start_visual("session", object())
    assert result["ok"] is False
    assert "private" not in result["error"]
    assert visual.revoked
    assert instance._thread is None


def test_visual_enrollment_timeout_reports_sensor_without_capture():
    visual = Visual()

    async def unavailable():
        raise TimeoutError("private DBus payload must never escape")

    visual.enroll = unavailable
    instance = watch.ComputerWatch(
        allowed=lambda: True, visual_factory=lambda *_a, **_k: visual
    )
    result = instance.start_visual("session", object())
    assert result["cause_code"] == "selected_window_sensor_timeout"
    assert "no image captured" in result["error"].lower()
    assert "private" not in result["error"]
    assert instance._thread is None and visual.revoked


def test_visual_enrollment_preserves_vetted_sensor_error_only():
    from screen_understanding import ScreenError
    from system.computer_focus import ComputerFocusError

    for error, expected in (
        (
            ComputerFocusError("KWin is unavailable on the configured bus."),
            "KWin is unavailable",
        ),
        (
            ScreenError("The selected window changed. Ask again."),
            "selected window changed",
        ),
        (
            ScreenError("PRIVATE-API-TOKEN-from-untrusted-exception"),
            "Selected window could not be verified",
        ),
    ):
        visual = Visual()

        async def unavailable(_error=error):
            raise _error

        visual.enroll = unavailable
        instance = watch.ComputerWatch(
            allowed=lambda: True, visual_factory=lambda *_a, _visual=visual, **_k: _visual
        )
        result = instance.start_visual("session", object())
        assert expected in result["error"]
        assert "PRIVATE" not in result["error"]
        assert "no image captured" in result["error"].lower()
        assert not instance.status()["active"] and visual.revoked


def test_disclosure_identity_is_passed_to_enrollment_before_worker(monkeypatch):
    visual = Visual()
    targets = []

    async def enroll(*, expected_target):
        targets.append(expected_target)
        raise RuntimeError("Disclosure target changed")

    visual.enroll = enroll
    instance = watch.ComputerWatch(
        allowed=lambda: True, visual_factory=lambda *_a, **_k: visual
    )
    target = ("window-id", "Editor", 123)
    assert not instance.start_visual("session", object(), expected_target=target)["ok"]
    assert targets == [target]
    assert instance._thread is None
    assert visual.revoked


def test_input_dialog_between_running_and_finished_still_reports_finish():
    visual = Visual(observation("running"))
    notices = []
    instance = engine(visual, notices)
    instance.tick(instance._generation)
    visual.result = observation("input_needed")
    instance.tick(instance._generation)
    visual.result = observation("completed")
    instance.tick(instance._generation)
    assert len(notices) == 2
    assert instance.status()["events"][-1]["state"] == "completed"
    assert (
        instance.status()["events"][-1]["source"] == "selected_window_visual_inference"
    )
    assert "Observed window evidence" in instance.briefing()


def test_activity_completion_survives_visual_occlusion():
    visual = Visual()

    async def blocked(*_a, **_k):
        raise RuntimeError("window occluded")

    visual.observe = blocked
    notices = []
    instance = engine(visual, notices)
    state = ["running"]
    instance._sample = lambda: {
        "activities": [{"id": "build", "title": "Build", "status": state[0]}]
    }
    instance.tick(instance._generation)
    state[0] = "done"
    instance.tick(instance._generation)
    instance.tick(instance._generation)
    assert len(notices) == 1
    assert instance.status()["events"][-1]["source"] == "activity_producer"
    assert instance.status()["state"] == "degraded"


def test_same_task_visual_and_activity_completion_notifies_once():
    visual = Visual(observation("running"))
    notices = []
    instance = engine(visual, notices)
    state = ["running"]
    instance._sample = lambda: {
        "activities": [{"id": "build", "title": "Build", "status": state[0]}]
    }
    instance.tick(instance._generation)
    visual.result = observation("completed")
    instance.tick(instance._generation)
    state[0] = "done"
    instance.tick(instance._generation)
    assert len(notices) == 1
    assert instance.status()["events"][-1]["source"] == "activity_producer"


def test_blocked_notifier_does_not_delay_stop_and_queued_notices_cancel():
    entered, release = threading.Event(), threading.Event()
    emitted = []

    def blocked(text):
        entered.set()
        release.wait(2)
        emitted.append(text)

    visual = Visual(observation("input_needed"))
    instance = engine(visual, [])
    instance._deliver = blocked
    instance.tick(instance._generation)
    assert entered.wait(1)
    visual.result = {**observation("input_needed"), "evidence": "Second visible dialog"}
    instance.tick(instance._generation)
    started = time.monotonic()
    instance.stop()
    assert time.monotonic() - started < 0.2
    release.set()
    for future in list(instance._notices):
        if not future.cancelled():
            future.result(timeout=1)
    assert len(emitted) == 1
    assert "private" not in emitted[0]


def test_metadata_session_cannot_upgrade_without_stop():
    instance = watch.ComputerWatch(allowed=lambda: True)
    instance._active = True
    result = instance.start_visual("session", object())
    assert not result["ok"]
    assert "Stop" in result["error"]
    assert instance._visual is None


def test_expired_visual_status_revokes_without_another_capture():
    visual = Visual()
    visual.status = lambda: {"expired": True}
    instance = engine(visual, [])
    instance._task = "Private work"
    status = instance.status()
    assert visual.revoked
    assert status["active"] is False
    assert status["task"] == ""
    assert "expired" in status["error"]


def test_stop_wins_against_blocked_visual_enrollment():
    entered, release = threading.Event(), threading.Event()
    visual = Visual()

    async def enroll():
        entered.set()
        release.wait(2)

    visual.enroll = enroll
    instance = watch.ComputerWatch(
        allowed=lambda: True, visual_factory=lambda *_a, **_k: visual
    )
    results = []
    worker = threading.Thread(
        target=lambda: results.append(instance.start_visual("session", object()))
    )
    worker.start()
    assert entered.wait(1)
    started = time.monotonic()
    instance.stop()
    assert time.monotonic() - started < 0.2
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert not results[0]["ok"]
    assert instance._thread is None
    assert not instance.status()["active"]
    assert visual.revoked


def test_trusted_consent_epoch_invalidated_during_enrollment_cannot_start():
    valid = [True]
    visual = Visual()

    async def enroll():
        valid[0] = False

    visual.enroll = enroll
    instance = watch.ComputerWatch(
        allowed=lambda: True, visual_factory=lambda *_a, **_k: visual
    )
    result = instance.start_visual("session", object(), consent_valid=lambda: valid[0])
    assert not result["ok"]
    assert instance._thread is None
    assert visual.revoked


def test_cancelled_trusted_ingress_never_constructs_observer():
    def factory(*_a, **_k):
        raise AssertionError("Observer must not be constructed after Stop")

    instance = watch.ComputerWatch(allowed=lambda: True, visual_factory=factory)
    assert not instance.start_visual("session", object(), consent_valid=lambda: False)[
        "ok"
    ]
