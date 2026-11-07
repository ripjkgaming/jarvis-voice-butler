"""power_actions.lock_state and the lock-sessions argv."""

import subprocess
import sys

sys.path.insert(0, "src")

import power_actions


def _run(rc=0, out="(true,)", exc=None):
    seen = []

    def run(argv, **kw):
        seen.append((list(argv), kw))
        if exc:
            raise exc
        return subprocess.CompletedProcess(argv, rc, out, "")

    return run, seen


def test_lock_argv_is_plural():
    assert power_actions.argv("lock") == ("loginctl", "lock-sessions")


def test_lock_state_reads_screensaver_with_timeout():
    run, seen = _run(out="(true,)\n")
    assert power_actions.lock_state(run) is True
    argv, kw = seen[0]
    assert argv == list(power_actions.LOCK_READ_ARGV)
    assert kw["timeout"] == 2.0


def test_lock_state_false_and_unknown():
    assert power_actions.lock_state(_run(out="(false,)")[0]) is False
    assert power_actions.lock_state(_run(rc=1)[0]) is None
    assert power_actions.lock_state(_run(out="garbage")[0]) is None
    assert (
        power_actions.lock_state(_run(exc=subprocess.TimeoutExpired("g", 2))[0]) is None
    )
