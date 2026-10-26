from types import SimpleNamespace

from latency import VoiceLatencyObserver, install_session_timing


def test_rollup_is_bounded_and_keeps_measurement_boundaries():
    observer = VoiceLatencyObserver()
    for i in range(300):
        start = i * 10.0
        observer.user_state_changed("speaking", now=start)
        token = observer.workflow_token
        observer.user_state_changed("listening", now=start + 1)
        observer.agent_state_changed("thinking", now=start + 2)
        observer.agent_state_changed("speaking", now=start + 3)
        line = observer.workflow_verified(token, now=start + 4)
        assert "vad_end_to_verified_workflow_s=3.000000" in line
    summary = observer.summary()
    assert summary["vad_end_to_agent_speaking_s"] == {"n": 256, "p50": 2.0, "p95": 2.0}
    assert summary["vad_end_to_verified_workflow_s"] == {
        "n": 256,
        "p50": 3.0,
        "p95": 3.0,
    }


def test_completion_requires_current_utterance_endpoint_and_explicit_verification():
    observer = VoiceLatencyObserver()
    observer.user_state_changed("speaking", now=1)
    token = observer.workflow_token
    assert observer.workflow_verified(token, now=2) is None
    observer.user_state_changed("listening", now=3)
    observer.user_state_changed("speaking", now=4)
    observer.user_state_changed("listening", now=5)
    assert observer.workflow_verified(token, now=6) is None
    token = observer.workflow_token
    assert observer.workflow_verified(token, now=float("nan")) is None
    assert observer.workflow_verified(token, now=4) is None
    assert observer.workflow_verified(token, now=7) is not None
    assert observer.workflow_verified(token, now=8) is None
    assert observer.summary()["vad_end_to_verified_workflow_s"]["n"] == 1


def test_install_is_idempotent_and_close_reports_only_numeric_summaries():
    class Session:
        def __init__(self):
            self.handlers = {}

        def on(self, name, callback):
            self.handlers.setdefault(name, []).append(callback)

    session = Session()
    logs = []
    observer = install_session_timing(session, lambda *line: logs.append(line))
    assert install_session_timing(session, lambda *line: logs.append(line)) is observer
    observer.user_state_changed("speaking", now=1)
    observer.user_state_changed("listening", now=2)
    observer.agent_state_changed("speaking", now=3)
    for handler in session.handlers["close"]:
        handler(SimpleNamespace())
    assert len(logs) == 1
    assert "p50_s=1.000000 p95_s=1.000000" in logs[0][1]
    assert "scope=state_events_not_acoustic_e2e" in logs[0][1]
