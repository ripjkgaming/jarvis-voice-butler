"""Decision retries honor one latency budget and actual provider cooldowns."""

import io
import itertools
import json
import urllib.error

import pytest

import mercury_decide


@pytest.fixture(autouse=True)
def isolated_decider(monkeypatch):
    monkeypatch.setenv("JARVIS_MERCURY", "1")
    monkeypatch.setattr(mercury_decide, "api_keys", lambda: ["first", "second"])
    monkeypatch.setattr(mercury_decide, "_rest_until", {})
    monkeypatch.setattr(mercury_decide, "_turn", itertools.count())


def test_exhausted_keys_are_not_retried_until_cooldown_expires(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(mercury_decide.time, "monotonic", lambda: clock[0])
    calls = []

    def quota(req, timeout):
        calls.append(req.headers["Authorization"])
        raise urllib.error.HTTPError(req.full_url, 429, "limit", {}, None)

    assert "quota spent" in mercury_decide.decide("state", {}, opener=quota)[1]
    assert len(calls) == 2
    assert "quota spent" in mercury_decide.decide("state", {}, opener=quota)[1]
    assert len(calls) == 2
    clock[0] += mercury_decide.COOLDOWN_S
    mercury_decide.decide("state", {}, opener=quota)
    assert len(calls) == 4


def test_fallback_receives_only_remaining_request_budget(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(mercury_decide.time, "monotonic", lambda: clock[0])
    timeouts = []

    def opener(req, timeout):
        timeouts.append(timeout)
        if len(timeouts) == 1:
            clock[0] += 2.0
            raise urllib.error.URLError("temporary failure")
        return io.BytesIO(json.dumps({"answers": {"ok": {"noul": 1}}}).encode())

    answer, warning = mercury_decide.decide("state", {}, timeout=3.0, opener=opener)
    assert warning is None and answer["ok"]["noul"] == 1
    assert timeouts == [3.0, 1.0]


def test_budget_exhaustion_does_not_start_another_request(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(mercury_decide.time, "monotonic", lambda: clock[0])
    calls = []

    def opener(req, timeout):
        calls.append(req)
        clock[0] += 3.0
        raise urllib.error.URLError("timed out")

    answer, warning = mercury_decide.decide("state", {}, timeout=3.0, opener=opener)
    assert answer is None and warning
    assert len(calls) == 1


def test_answer_arriving_after_deadline_is_discarded(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(mercury_decide.time, "monotonic", lambda: clock[0])

    def opener(req, timeout):
        clock[0] += 4.0
        return io.BytesIO(b'{"answers": {"late": {"noul": 1}}}')

    answer, warning = mercury_decide.decide("state", {}, timeout=3.0, opener=opener)
    assert answer is None and "timed out" in warning.lower()


def test_late_quota_response_still_rests_the_exhausted_key(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(mercury_decide.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mercury_decide, "api_keys", lambda: ["first"])
    calls = []

    def quota(req, timeout):
        calls.append(req)
        clock[0] += 4.0
        raise urllib.error.HTTPError(req.full_url, 429, "limit", {}, None)

    answer, warning = mercury_decide.decide("state", {}, timeout=3.0, opener=quota)
    assert answer is None and "timed out" in warning.lower()
    answer, warning = mercury_decide.decide("state", {}, timeout=3.0, opener=quota)
    assert answer is None and "quota spent" in warning.lower()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "timeout", [True, False, 0, -1, float("inf"), float("nan"), "3", None]
)
def test_invalid_timeout_fails_without_dispatch(timeout):
    def opener(*args, **kwargs):
        pytest.fail("invalid budget dispatched a request")

    answer, warning = mercury_decide.decide("state", {}, timeout=timeout, opener=opener)
    assert answer is None and warning


def test_unserializable_state_is_fail_soft():
    answer, warning = mercury_decide.decide({"bad": object()}, {})
    assert answer is None and warning
