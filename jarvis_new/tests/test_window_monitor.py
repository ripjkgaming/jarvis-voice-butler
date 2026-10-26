import dataclasses
import os

import pytest

import build_monitor as bm
import jobs
import window_monitor as wm

W1 = "{11111111-1111-1111-1111-111111111111}"
W2 = "{22222222-2222-2222-2222-222222222222}"


class FakeWindows:
    def __init__(self):
        self.rows = {W1: wm.Window(W1, "compile — VS Code", "code", 42, ":1.7")}
        self.failed = False

    def find(self, name):
        if self.failed:
            raise wm.WindowError("metadata unavailable")
        return [r for r in self.rows.values() if wm.matches(name, r)]

    def current(self, wid):
        if self.failed:
            raise wm.WindowError("metadata unavailable")
        return self.rows.get(wid)


@pytest.fixture
def setup(tmp_path):
    windows = FakeWindows()
    identities = {
        42: bm.Identity("boot", os.getuid(), 42, 123, "S"),
        44: bm.Identity("boot", os.getuid(), 44, 200, "S"),
    }

    def read(pid):
        value = identities.get(pid)
        if isinstance(value, Exception):
            raise value
        return value

    mon = bm.Monitor(jobs.Registry(tmp_path / "registry"), proc_reader=read)
    resolver = wm.Resolver(mon, windows=windows, proc_reader=read, clock=lambda: 10)
    return resolver, mon, windows, identities


def test_no_ide_process_guessed_as_build(setup):
    resolver, mon, _, _ = setup
    result = resolver.select("VS Code", "r")
    assert result["status"] == "scope_required"
    assert mon.registry.list(bm.OWNER) == []
    assert mon.registry.list(wm.OWNER) == []
    assert "PID" not in result["say"]


def test_registered_exact_identity_mapped(setup):
    resolver, mon, _, _ = setup
    row = mon.register("existing", 42, "compile")
    result = resolver.select("VS Code", "request")
    assert result["status"] == "registered_build"
    assert result["job"]["id"] == row["id"]
    assert result["observation_scope"] == "registered_process"
    assert len(mon.registry.list(bm.OWNER)) == 1


def test_registered_child_without_attestation_not_guessed(setup):
    resolver, mon, _, identities = setup
    identities[43] = bm.Identity("boot", os.getuid(), 43, 200, "S")
    mon.register("child", 43, "child build")
    assert resolver.select("VS Code", "r")["status"] == "scope_required"


def test_ambiguity_and_stable_choice(setup):
    resolver, _, windows, _ = setup
    windows.rows[W2] = wm.Window(W2, "compile — VS Code", "code", 44, ":1.7")
    result = resolver.select("VS Code", "r")
    assert result["status"] == "needs_selection" and len(result["choices"]) == 2
    assert all("pid" not in item and "id" not in item for item in result["choices"])
    assert resolver.choose(2, "r2")["status"] == "scope_required"


def test_choice_process_reuse_is_refused(setup):
    resolver, _, windows, identities = setup
    windows.rows[W2] = wm.Window(W2, "other — VS Code", "code", 44, ":1.7")
    identities[44] = bm.Identity("boot", os.getuid(), 44, 200, "S")
    resolver.select("VS Code", "r")
    identities[44] = bm.Identity("boot", os.getuid(), 44, 999, "S")
    assert resolver.choose(2, "r2")["status"] == "selection_changed"


def test_explicit_window_activity_title_change_and_close(setup):
    resolver, mon, windows, _ = setup
    result = resolver.select("VS Code", "r", scope="window_activity")
    assert result["status"] == "registered_window_activity"
    row = mon.registry.get(result["job"]["id"])
    assert (
        row["owner"] == wm.OWNER
        and row["payload"]["observation_scope"] == "window_activity"
    )
    windows.rows[W1] = dataclasses.replace(windows.rows[W1], title="new title")
    observed = resolver.status(result["job"]["id"])
    assert observed["job"]["status"] == "window_present"
    windows.rows.clear()
    observed = resolver.status(result["job"]["id"])
    assert observed["job"]["status"] == "window_closed"
    assert observed["job"]["outcome"] == "ended_unknown"
    assert "success" in observed["say"]


def test_wayland_failure_unknown_not_closed(setup):
    resolver, _, windows, _ = setup
    row = resolver.select("VS Code", "r", scope="window_activity")["job"]
    windows.failed = True
    assert resolver.status(row["id"])["job"]["status"] == "observation_unknown"


def test_wrong_owner_pid_missing_and_browser(setup):
    resolver, _, windows, identities = setup
    identities[42] = bm.Identity("boot", os.getuid() + 1, 42, 123, "S")
    assert resolver.select("VS Code", "r")["status"] == "unavailable"
    identities[42] = bm.Identity("boot", os.getuid(), 42, 123, "S")
    windows.rows[W1] = wm.Window(W1, "Cloud build — Brave", "brave", 42, ":1.7")
    result = resolver.select("Brave", "r")
    assert result["status"] == "scope_required" and "cloud" in result["say"]
    windows.rows[W1] = dataclasses.replace(windows.rows[W1], pid=0)
    assert resolver.select("Brave", "r")["status"] == "unavailable"


def test_window_service_reuse_and_exit(setup):
    resolver, _, windows, _identities = setup
    row = resolver.select("VS Code", "r", scope="window_activity")["job"]
    windows.rows[W1] = dataclasses.replace(windows.rows[W1], service_owner=":1.999")
    assert resolver.status(row["id"])["job"]["reason"] == "window_identity_changed"
    assert resolver.status(row["id"])["job"]["outcome"] == "ended_unknown"


def test_selection_and_process_reads_cannot_disclose_pid(setup):
    resolver, _, _, _ = setup
    row = resolver.select("VS Code", "r", scope="window_activity")
    serialized = str(row)
    assert "start_ticks" not in serialized and "boot_id" not in serialized


def test_exact_title_beats_partial_app(setup):
    resolver, _, windows, identities = setup
    windows.rows[W2] = wm.Window(W2, "other — VS Code", "code", 44, ":1.7")
    identities[44] = bm.Identity("boot", os.getuid(), 44, 200, "S")
    assert resolver.select("compile — VS Code", "r")["status"] == "scope_required"


def test_attested_registered_child_and_window_close_keep_build_running(setup):
    resolver, mon, windows, identities = setup
    identities[43] = bm.Identity("boot", os.getuid(), 43, 456, "S")
    row = mon.register("child", 43, "compile")
    current = mon.registry.get(row["id"])
    data = dict(current["payload"])
    data["window_binding"] = {
        "window_id": W1,
        "service_owner": ":1.7",
        "identity": dataclasses.asdict(identities[42]),
    }
    mon.registry.update(row["id"], bm.OWNER, data, current["revision"])
    result = resolver.select("VS Code", "r")
    assert result["status"] == "registered_build"
    windows.rows.clear()
    got = resolver.status(row["id"])
    assert got["window_state"] == "window_closed"
    assert got["job"]["status"] == "running" and got["job"]["outcome"] is None


def test_original_query_retained_after_title_change(setup):
    resolver, _, windows, _ = setup
    row = resolver.select("compile — VS Code", "r", scope="window_activity")["job"]
    windows.rows[W1] = dataclasses.replace(windows.rows[W1], title="changed")
    assert resolver.status_by_name("compile — VS Code")["job"]["id"] == row["id"]


def test_current_metadata_parser_and_read_only_bus_calls(monkeypatch):
    import json
    from types import SimpleNamespace

    raw = json.dumps(
        {
            "type": "a{sv}",
            "data": [
                {
                    "uuid": {"type": "s", "data": [W1]},
                    "pid": {"type": "u", "data": [42]},
                    "caption": {"type": "s", "data": ["compile — VS Code"]},
                    "resourceClass": {"type": "s", "data": ["code"]},
                    "localhost": {"type": "b", "data": [True]},
                }
            ],
        }
    )
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["timeout"] == 1 and kwargs["capture_output"]
        if argv[-3] == "GetNameOwner":
            return SimpleNamespace(
                returncode=0, stdout=json.dumps({"type": "s", "data": [":1.7"]})
            )
        return SimpleNamespace(returncode=0, stdout=raw)

    current = wm.KWinWindows(run=run).current(W1)
    assert current.pid == 42 and current.id == W1
    assert {argv[-3] for argv in calls} == {"GetNameOwner", "getWindowInfo"}
    assert "queryWindowInfo" not in str(calls) and "Run" not in str(calls)
    malformed = json.loads(raw)
    malformed["data"][0]["localhost"]["data"] = [False]
    with pytest.raises(wm.WindowError):
        wm.parse_info(json.dumps(malformed), W1, ":1.7")


def test_kwin_metadata_failure_is_not_empty_window_list(monkeypatch):
    from types import SimpleNamespace

    def run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="")

    with pytest.raises(wm.WindowError):
        wm.KWinWindows(run=run).current(W1)


def test_corrupt_activity_scope_fails_closed(setup):
    resolver, mon, _, _ = setup
    row = resolver.select("VS Code", "r", scope="window_activity")["job"]
    actual = mon.registry.get(row["id"])
    mon.registry.update(row["id"], wm.OWNER, {}, actual["revision"])
    with pytest.raises(wm.WindowError):
        resolver.status(row["id"])


def test_pending_choice_expired_and_process_denied(setup):
    resolver, _, windows, identities = setup
    windows.rows[W2] = wm.Window(W2, "other — VS Code", "code", 44, ":1.7")
    resolver.select("VS Code", "r")
    resolver.clock = lambda: 100
    assert resolver.choose(1, "next")["status"] == "needs_selection"
    identities[42] = PermissionError("private sentinel")
    assert resolver.select("compile — VS Code", "r")["status"] == "unavailable"


def test_named_window_observation_keeps_goodnight_fail_closed(setup, tmp_path):
    from goodnight_supported import SupportedInventory

    # Real registry metadata, fake Goodnight readers/fence: a new scope cannot
    # accidentally establish idle. Verify the installed reader rejects it.
    resolver, mon, _, _ = setup
    resolver.select("VS Code", "r", scope="window_activity")
    import goodnight_supported

    class Fence:
        def work_clear(self):
            return True

    old = goodnight_supported.sql_rows
    try:
        goodnight_supported.sql_rows = lambda path, **kw: mon.registry.list(wm.OWNER)
        root = tmp_path / "scope"
        root.mkdir()
        (root / "job_registry").mkdir()
        (root / "job_registry/jobs.sqlite3").touch()
        assert SupportedInventory(root, Fence()).clear() is None
    finally:
        goodnight_supported.sql_rows = old


def test_owner_explicit_registered_label_association_revalidates_build(setup):
    resolver, mon, windows, identities = setup
    row = mon.register("known-child", 44, "compiler")
    out = resolver.select("VS Code", "association", build_label="compiler")
    assert out["status"] == "registered_build"
    saved = mon.registry.get(row["id"])
    assert saved["payload"]["identity"]["pid"] == 44
    assert saved["payload"]["window_binding"]["identity"]["pid"] == 42
    windows.rows.clear()
    result = resolver.status(row["id"])
    assert result["window_state"] == "window_closed"
    assert result["job"]["status"] == "running"
    identities[44] = None
    assert resolver.status(row["id"])["job"]["outcome"] == "ended_unknown"


def test_owner_explicit_association_dead_or_reused_build_refused(setup):
    resolver, mon, _, identities = setup
    mon.register("known", 44, "compiler")
    identities[44] = bm.Identity("boot", os.getuid(), 44, 999, "S")
    assert (
        resolver.select("VS Code", "r", build_label="compiler")["status"]
        == "scope_required"
    )
    assert "window_binding" not in mon.registry.list(bm.OWNER)[0]["payload"]


def test_duplicate_registered_labels_never_choose_latest(setup):
    resolver, mon, _, _ = setup
    row = mon.register("old", 44, "compiler")
    mon.stop_monitoring(row["id"])
    mon.register("new", 42, "compiler")
    assert (
        resolver.select("VS Code", "r", build_label="compiler")["status"]
        == "needs_selection"
    )


def test_new_explicit_request_resumes_same_active_window(setup):
    resolver, mon, _, _ = setup
    first = resolver.select("VS Code", "old", scope="window_activity")
    second = resolver.select("VS Code", "fresh", scope="window_activity")
    assert first["job"]["id"] == second["job"]["id"]
    assert len(mon.registry.list(wm.OWNER)) == 1
