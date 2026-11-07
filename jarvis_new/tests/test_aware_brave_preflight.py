"""Read-only Phase 4 diagnostics: no browser or desktop operations."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(**overrides):
    args = {
        "probe": {
            "cdp": True,
            "browser": "test-browser",
            "worker": "chrome-extension://known/jarvis-tabs.js",
        },
        "listeners": ["127.0.0.1:9222"],
        "screens": [
            {"name": "main", "x": 0, "y": 0, "w": 1920, "h": 1080},
            {"name": "side", "x": 1920, "y": 0, "w": 1920, "h": 1080},
            {"name": "hud", "x": 3840, "y": 0, "w": 1920, "h": 1080},
        ],
        "main": "main",
        "hud": "hud",
    }
    args.update(overrides)
    return load_script("aware_brave_preflight").build_report(**args)


def test_preflight_names_task_monitor_without_opening_a_tab():
    data = report()
    assert data["ready_for_existing_tabs"] is True
    assert data["placement"] == {"mode": "monitor", "screen": "side", "rect": None}
    assert data["mutated"] is False


@pytest.mark.parametrize(
    "listeners",
    [[], ["0.0.0.0:9222"], ["[::]:9222"], ["192.168.1.2:9222"], ["unknown"]],
)
def test_preflight_refuses_unknown_or_exposed_cdp_listener(listeners):
    data = report(listeners=listeners)
    assert data["ready_for_existing_tabs"] is False
    assert "cdp_listener_not_verified_loopback" in data["blockers"]


def test_preflight_accepts_ipv6_loopback():
    assert report(listeners=["[::1]:9222"])["ready_for_existing_tabs"] is True


def test_preflight_reports_missing_worker_without_waking_it():
    data = report(probe={"cdp": True, "worker": None})
    assert data["ready_for_existing_tabs"] is False
    assert data["blockers"] == ["no_tab_worker"]


def test_preflight_single_output_reports_minimised_fallback():
    data = report(
        screens=[{"name": "main", "x": 0, "y": 0, "w": 1920, "h": 1080}], hud="main"
    )
    assert data["placement"]["mode"] == "minimise"
    assert "minimised_timing_unverified" in data["manual_checks"]


def test_spike_worker_runs_only_the_throwaway_move_test():
    module = load_script("aware_spike")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return type(
            "Result", (), {"returncode": 0, "stdout": "1 passed", "stderr": ""}
        )()

    result = module.cmd_worker(runner=run)
    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0][0][1:3] == ["-m", "pytest"]
    assert "test_live_takeover_keeps_state_moves_back_and_never_reloads" in " ".join(
        calls[0][0]
    )
    assert result["kwin"] == "mocked"


def test_spike_worker_failure_does_not_claim_success():
    module = load_script("aware_spike")
    result = module.cmd_worker(
        runner=lambda *a, **k: type(
            "Result", (), {"returncode": 1, "stdout": "failed", "stderr": ""}
        )()
    )
    assert result["ok"] is False
