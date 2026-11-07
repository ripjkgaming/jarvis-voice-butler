"""Independent Linux bridge memory guard; never targets other applications.

Detect sustained RAM + swap excess, save a bounded stack report, and let the
existing Jarvis shell supervisor recover the bridge. No speculative code edits.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import logging
import logging.handlers
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

LOG = logging.getLogger("bridge-memory-guard")


def status(pid: int, proc: Path = Path("/proc")) -> dict[str, str]:
    return dict(
        line.split(":", 1)
        for line in (proc / str(pid) / "status").read_text().splitlines()
        if ":" in line
    )


def footprint(pid: int, proc: Path = Path("/proc")) -> int:
    data = status(pid, proc)
    return sum(
        int(data.get(key, "0 kB").split()[0]) * 1024 for key in ("VmRSS", "VmSwap")
    )


def bridge_pids(script: Path, python: Path, proc: Path = Path("/proc")) -> list[int]:
    wanted = [os.fsencode(python), os.fsencode(script), b""]
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            if (entry / "cmdline").read_bytes().split(b"\0") != wanted:
                continue
            parent = int(status(int(entry.name), proc)["PPid"])
            # A supervised child is essential: recovery must not leave Jarvis down.
            if (proc / str(parent) / "comm").read_text().strip() == "jarvis-shell":
                found.append(int(entry.name))
        except (OSError, ValueError, KeyError):
            continue
    return sorted(found)


class Policy:
    def __init__(self, limit=2 * 1024**3, consecutive=3, cooldown=600, hourly_limit=3):
        self.limit = limit
        self.consecutive = consecutive
        self.cooldown = cooldown
        self.hourly_limit = hourly_limit
        self.strikes: dict[int, int] = {}
        self.recoveries: collections.deque[float] = collections.deque()

    def observe(self, pid: int, size: int, now: float) -> bool:
        # Only one bridge is expected, but keep identity-specific counters.
        self.strikes = {pid: self.strikes.get(pid, 0) + 1 if size > self.limit else 0}
        while self.recoveries and now - self.recoveries[0] >= 3600:
            self.recoveries.popleft()
        cooled = not self.recoveries or now - self.recoveries[-1] >= self.cooldown
        return (
            self.strikes[pid] >= self.consecutive
            and cooled
            and len(self.recoveries) < self.hourly_limit
        )

    def recovered(self, now: float) -> None:
        self.recoveries.append(now)
        self.strikes.clear()


def diagnose(pid: int, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {"pid": pid, "time": time.time(), "status": status(pid)}
    spy = shutil.which("py-spy") or str(Path.home() / ".local/bin/py-spy")
    try:
        result = subprocess.run(
            [spy, "dump", "--pid", str(pid)], capture_output=True, text=True, timeout=10
        )
        report["stacks"] = (result.stdout + result.stderr)[:131072]
    except (OSError, subprocess.TimeoutExpired) as exc:
        report["profiler_error"] = str(exc)
    path = folder / f"bridge-{time.time_ns()}.json"
    path.write_text(json.dumps(report, indent=2))
    path.chmod(0o600)
    for old in sorted(folder.glob("bridge-*.json"))[:-5]:
        old.unlink(missing_ok=True)


def recover(pid: int, script: Path, python: Path, folder: Path) -> bool:
    # pidfd prevents PID reuse from ever signaling an unrelated process.
    fd = os.pidfd_open(pid)
    try:
        if pid not in bridge_pids(script, python):
            return False
        try:
            diagnose(pid, folder)
        except Exception:
            LOG.exception("Diagnostic capture failed; continuing memory recovery")
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        # SIGTERM is Python's default immediate termination; allow five seconds
        # before forcing a stuck native call to release the memory.
        import select

        poll = select.poll()
        poll.register(fd, select.POLLIN)
        if not poll.poll(5000):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        LOG.warning("Recovered bridge pid=%s; shell supervisor will relaunch it", pid)
        return True
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-mib", type=int, default=2048)
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    if args.limit_mib < 256 or args.interval < 1:
        parser.error("limit must be >=256 MiB and interval >=1 second")
    repo = Path(__file__).resolve().parent.parent
    script, python = repo / "src/bridge.py", repo / ".venv/bin/python"
    home = Path(os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
    folder = home / "memory-guard"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = logging.handlers.RotatingFileHandler(
        folder / "guard.log", maxBytes=1024**2, backupCount=2
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    LOG.addHandler(logging.StreamHandler())
    LOG.setLevel(logging.INFO)
    policy = Policy(limit=args.limit_mib * 1024**2)
    # Preserve the restart budget across monitor restarts (including reboot).
    state_path = folder / "recoveries.json"
    with contextlib.suppress(OSError, ValueError, TypeError):
        policy.recoveries.extend(
            float(t) for t in json.loads(state_path.read_text())[-3:]
        )
    LOG.info(
        "Guard active: limit=%s MiB, three consecutive samples, cooldown=600s, max=3/hour",
        args.limit_mib,
    )
    last_summary = 0.0
    while True:
        try:
            now = time.time()
            pids = bridge_pids(script, python)
            for pid in pids:
                size = footprint(pid)
                if now - last_summary >= 60:
                    LOG.info("Bridge pid=%s RAM+swap=%.1f MiB", pid, size / 1024**2)
                    last_summary = now
                if policy.observe(pid, size, now):
                    LOG.warning(
                        "Sustained excess: pid=%s RAM+swap=%.1f MiB",
                        pid,
                        size / 1024**2,
                    )
                    if recover(pid, script, python, folder):
                        policy.recovered(now)
                        tmp = state_path.with_suffix(".tmp")
                        tmp.write_text(json.dumps(list(policy.recoveries)))
                        tmp.replace(state_path)
                elif size > policy.limit and policy.strikes.get(pid, 0) == 3:
                    LOG.error("Bridge over limit; recovery cooldown/budget active")
            if not pids:
                policy.strikes.clear()
        except Exception:
            LOG.exception("Guard sample failed; retrying")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
