"""Guest mode must end when watch mode ends, by every path.

Root causes covered here (see watch_mode.enter_guest docs):
* a stop left Jarvis in guest until the stopping process finished stitching,
  because guest_required() also counted the still-alive process;
* a watch that was killed or crashed left the guest latch on disk forever;
* guest state had no owner, so an independent (explicit) guest could not
  coexist with a watch without being cleared or locking the screen.
"""

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

import watch_mode


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    # Never shell out to loginctl from tests.
    monkeypatch.setattr(watch_mode, "session_locked", lambda: False, raising=False)


@pytest.fixture
def live_watch(tmp_path):
    """A real process whose argv ends in watch_mode.py, registered as the watch."""
    script = tmp_path / "watch_mode.py"
    script.write_text("import time\ntime.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, str(script)])
    watch_mode.state_dir().mkdir(parents=True, exist_ok=True)
    watch_mode.pid_path().write_text(str(proc.pid))
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait()


def _age_guest(seconds: float) -> None:
    """Backdate every origin in the guest latch."""
    old = time.time() - seconds
    watch_mode._write_guest(dict.fromkeys(watch_mode.guest_origins(), old))


# --- deactivation while the watch process is still alive (stitching) -------


def test_stop_ends_guest_even_while_process_is_still_saving(live_watch):
    watch_mode.enter_guest()
    assert watch_mode.guest_required()
    assert watch_mode.request_stop() is True
    # The process is alive and stitching the video; Sir already stopped it.
    assert watch_mode.is_running()
    assert not watch_mode.guest_required()


def test_card_close_ends_guest_even_while_process_is_still_saving(live_watch):
    watch_mode.enter_guest()
    screen = watch_mode.WatchScreen.__new__(watch_mode.WatchScreen)
    screen.closing = False
    screen.watcher = SimpleNamespace(recorder=SimpleNamespace(events=0))
    screen.panels = []
    screen.saver = None
    screen.root = SimpleNamespace(
        update_idletasks=lambda: None, after=lambda *a: None, quit=lambda: None
    )
    screen.close()
    assert watch_mode.is_running()
    assert not watch_mode.guest_required()


def test_new_launch_clears_a_previous_stopping_marker(live_watch, monkeypatch):
    watch_mode.enter_guest()
    watch_mode.request_stop()
    assert not watch_mode.guest_required()
    with monkeypatch.context() as m:
        m.setattr(watch_mode, "is_running", lambda: False)
        assert watch_mode.launch(popen=lambda *a, **k: None)["ok"] is True
    assert watch_mode.guest_required()


# --- stale state after kill / crash / restart ------------------------------


def test_stale_watch_guest_does_not_survive_when_no_watch_is_running():
    watch_mode.enter_guest()
    watch_mode.pid_path().write_text("999999999")  # dead pid left by a kill
    _age_guest(watch_mode.GUEST_STALE_S + 5)
    assert not watch_mode.guest_required()
    assert not watch_mode.guest_path().exists()


def test_fresh_guest_survives_the_launch_window_before_the_pid_exists():
    watch_mode.enter_guest()  # launch() latched; the child has not started yet
    assert not watch_mode.is_running()
    assert watch_mode.guest_required()


def test_stale_guest_is_kept_while_the_os_session_is_locked(monkeypatch):
    watch_mode.enter_guest()
    _age_guest(watch_mode.GUEST_STALE_S + 5)
    monkeypatch.setattr(watch_mode, "session_locked", lambda: True)
    assert watch_mode.guest_required()
    monkeypatch.setattr(watch_mode, "session_locked", lambda: False, raising=False)
    assert not watch_mode.guest_required()


def test_live_watch_is_never_stale_however_old(live_watch):
    watch_mode.enter_guest()
    _age_guest(10 * 3600)
    assert watch_mode.guest_required()
    assert watch_mode.guest_origins() == {"watch"}


def test_legacy_empty_guest_file_is_a_watch_latch():
    watch_mode.state_dir().mkdir(parents=True, exist_ok=True)
    watch_mode.guest_path().touch()
    assert watch_mode.guest_required()
    old = time.time() - watch_mode.GUEST_STALE_S - 5
    os.utime(watch_mode.guest_path(), (old, old))
    assert not watch_mode.guest_required()


def test_malformed_guest_file_is_a_watch_latch_not_a_crash():
    watch_mode.state_dir().mkdir(parents=True, exist_ok=True)
    watch_mode.guest_path().write_text("{not json")
    assert watch_mode.guest_required()
    assert watch_mode.guest_origins() == {"watch"}


# --- ownership: explicit guest is independent of the watch -----------------


def test_explicit_guest_survives_watch_stop(live_watch):
    watch_mode.enter_guest("explicit")
    watch_mode.enter_guest("watch")
    assert watch_mode.guest_origins() == {"watch", "explicit"}
    watch_mode.request_stop()
    assert watch_mode.guest_origins() == {"explicit"}
    assert watch_mode.guest_required()


def test_explicit_guest_is_never_stale_cleared():
    watch_mode.enter_guest("explicit")
    _age_guest(30 * 24 * 3600)
    assert watch_mode.guest_required()


def test_failed_spawn_keeps_an_existing_explicit_guest(monkeypatch):
    watch_mode.enter_guest("explicit")
    monkeypatch.setattr(watch_mode, "is_running", lambda: False)

    def boom(*a, **k):
        raise OSError("cannot spawn")

    assert watch_mode.launch(popen=boom)["ok"] is False
    assert watch_mode.guest_origins() == {"explicit"}


def test_leave_guest_removes_only_its_origin_and_deletes_empty_file():
    watch_mode.enter_guest("explicit")
    watch_mode.enter_guest("watch")
    assert watch_mode.leave_guest("watch") is True
    assert watch_mode.guest_origins() == {"explicit"}
    assert watch_mode.leave_guest("watch") is False
    assert watch_mode.leave_guest("explicit") is True
    assert not watch_mode.guest_path().exists()


def test_restore_owner_is_the_explicit_owner_override_for_all_origins():
    watch_mode.enter_guest("explicit")
    watch_mode.enter_guest("watch")
    watch_mode.restore_owner()
    assert not watch_mode.guest_required()


def test_unknown_origin_is_rejected():
    with pytest.raises(ValueError):
        watch_mode.enter_guest("phone-ish")


def test_guest_file_is_private_json_with_origins():
    watch_mode.enter_guest("explicit")
    path = watch_mode.guest_path()
    assert (path.stat().st_mode & 0o777) == 0o600
    assert set(json.loads(path.read_text())["origins"]) == {"explicit"}


# --- watchdog must only react to the watch's own latch ---------------------


def test_watchdog_does_not_lock_after_clean_stop_with_explicit_guest(monkeypatch):
    watch_mode.enter_guest("explicit")
    watch_mode.enter_guest("watch")
    watch_mode.leave_guest("watch")  # clean stop
    locks = []
    monkeypatch.setattr(watch_mode, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(watch_mode, "fail_safe_lock", lambda: locks.append(1) or True)
    watch_mode.guard_watch(123)
    assert locks == []
    assert watch_mode.guest_required()  # explicit guest still holds
