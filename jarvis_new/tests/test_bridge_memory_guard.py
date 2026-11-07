from bridge_memory_guard import Policy, bridge_pids, footprint


def test_counts_swap_as_well_as_resident_ram(tmp_path):
    p = tmp_path / "42"
    p.mkdir()
    (p / "status").write_text("VmRSS:\t100 kB\nVmSwap:\t200 kB\n")
    assert footprint(42, tmp_path) == 300 * 1024


def test_requires_sustained_excess_and_resets_after_recovery():
    policy = Policy(limit=100, consecutive=3, cooldown=60, hourly_limit=2)
    assert not policy.observe(1, 101, 0)
    assert not policy.observe(1, 90, 10)
    assert not policy.observe(1, 101, 20)
    assert not policy.observe(1, 101, 30)
    assert policy.observe(1, 101, 40)
    policy.recovered(40)
    assert not policy.observe(2, 101, 50)
    assert not policy.observe(2, 101, 60)
    assert not policy.observe(2, 101, 70)
    assert policy.observe(2, 101, 100)
    policy.recovered(100)
    assert not policy.observe(3, 101, 200)
    assert not policy.observe(3, 101, 210)
    assert not policy.observe(3, 101, 220)
    assert policy.observe(3, 101, 3701)


def test_new_process_does_not_inherit_strikes():
    policy = Policy(limit=100, consecutive=3)
    assert not policy.observe(1, 101, 0)
    assert not policy.observe(1, 101, 10)
    assert not policy.observe(2, 101, 20)


def test_only_exact_bridge_under_shell_is_eligible(tmp_path):
    script = tmp_path / "repo/src/bridge.py"
    python = tmp_path / "repo/.venv/bin/python"

    def process(pid, args, parent=5, comm="python"):
        p = tmp_path / str(pid)
        p.mkdir()
        (p / "cmdline").write_bytes(b"\0".join(str(a).encode() for a in args) + b"\0")
        (p / "status").write_text(f"PPid:\t{parent}\n")
        (p / "comm").write_text(comm)

    process(5, ["/repo/jarvis-shell"], comm="jarvis-shell")
    process(6, [python, script])
    process(7, [python, str(script) + ".other"])
    process(8, [python, script], parent=99)
    assert bridge_pids(script, python, tmp_path) == [6]


def test_recovery_terminates_child_and_captures_diagnostics(monkeypatch, tmp_path):
    import subprocess
    import sys

    import bridge_memory_guard as guard

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    captures = []
    monkeypatch.setattr(guard, "bridge_pids", lambda *a: [child.pid])
    monkeypatch.setattr(guard, "diagnose", lambda pid, folder: captures.append(pid))
    try:
        assert guard.recover(
            child.pid, tmp_path / "bridge.py", tmp_path / "python", tmp_path
        )
        assert child.wait(timeout=2) < 0
        assert captures == [child.pid]
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_recovery_refuses_process_that_no_longer_matches(monkeypatch, tmp_path):
    import os

    import bridge_memory_guard as guard

    monkeypatch.setattr(guard, "bridge_pids", lambda *a: [])
    assert not guard.recover(
        os.getpid(), tmp_path / "bridge.py", tmp_path / "python", tmp_path
    )
