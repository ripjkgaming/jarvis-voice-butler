"""Live adapters use source evidence and never enable an unverified lock."""

from aware import state


def test_screen_skip_resets_evidence_without_camera_call(tmp_path, monkeypatch):
    from aware.expansion_runtime import Runtime

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    state.update(enabled=True)
    seen = []

    class Policy:
        def observe(self, observation, now=None):
            seen.append(observation)
            return {
                "eligible": False,
                "reason": "uncertain_activity",
                "activity": "uncertain",
            }

    r = Runtime(
        policy=Policy(),
        camera=lambda: (_ for _ in ()).throw(AssertionError("camera unnecessary")),
        record=lambda **kw: True,
    )
    row = r.assess({"outcome": "skip", "reason": "sensitive"}, now=1000)
    assert not row["eligible"]
    assert seen[0]["activity"] == "uncertain"


def test_seen_activity_uses_original_observation_time_and_context(
    tmp_path, monkeypatch
):
    from aware.expansion_runtime import Runtime

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    state.update(enabled=True)
    seen = []

    class Policy:
        def observe(self, observation, now=None):
            seen.append(observation)
            return {"eligible": True, "reason": "eligible", "activity": "leisure"}

    memories = []
    r = Runtime(
        policy=Policy(),
        camera=lambda: {"status": "present", "timestamp": 900},
        record=lambda **kw: memories.append(kw),
        inhibitors=lambda: {
            "in_call": False,
            "presentation": False,
            "scheduled_break": False,
        },
    )
    decision = r.assess(
        {
            "outcome": "seen",
            "ts": 900,
            "tick_id": "abc",
            "summary": "Watching a comedy movie for fun",
            "activity": "media",
        },
        now=1000,
    )
    assert seen[0]["observed_at"] == 900
    assert seen[0]["presence_at"] == 900
    assert seen[0]["content"] == "Watching a comedy movie for fun"
    assert decision["mode"] == "observation_only"
    assert decision["would_intervene"] is True
    assert memories[0]["source"] == "screen:abc"


def test_off_controls_do_not_fetch_camera_or_write_memories(tmp_path, monkeypatch):
    from aware.expansion_runtime import Runtime

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    r = Runtime(
        camera=lambda: (_ for _ in ()).throw(AssertionError()),
        record=lambda **kw: (_ for _ in ()).throw(AssertionError()),
    )
    assert r.assess({"outcome": "seen"})["reason"] == "controls"


def test_health_readings_survive_vault_failure(tmp_path, monkeypatch):
    from aware.expansion_runtime import Runtime

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    state.update(enabled=True)

    def broken(**kwargs):
        raise OSError("vault unavailable")

    spoken = []
    result = Runtime(record=broken).health(
        {
            "timestamp": 1000,
            "readings": {"disk": {"status": "critical"}},
            "incidents": [{"key": "disk", "speech": "Disk space is critically low."}],
        },
        speak=spoken.append,
    )
    assert result["readings"]["disk"]["status"] == "critical"
    assert result["delivery_errors"] == ["OSError"]
    assert spoken == ["Disk space is critically low."]


def test_health_memory_retry_does_not_repeat_speech(tmp_path, monkeypatch):
    from aware.expansion_runtime import Runtime

    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    state.update(enabled=True)
    spoken = []

    def fail(**kw):
        raise OSError()

    first = Runtime(record=fail)
    assert (
        first.health(
            {"timestamp": 1000, "incidents": [{"key": "disk", "speech": "Low disk"}]},
            spoken.append,
        )["memory_retries"]
        == 1
    )
    delivered = []
    result = Runtime(record=lambda **kw: delivered.append(kw)).health(
        {"incidents": []}, spoken.append
    )
    assert result["memory_retries"] == 0
    assert delivered[0]["timestamp"] == 1000
    assert spoken == ["Low disk"]
