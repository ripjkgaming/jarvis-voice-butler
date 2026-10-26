"""Independent collection worker. No service install or live dispatch."""

import argparse
import fcntl
import os
import signal
import threading
from pathlib import Path

from .adapters import F1Adapters
from .core import Store, Worker
from .triage import FreeTriage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--home", type=Path, required=True, help="Existing private Jarvis home"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    home = args.home.resolve(strict=True)
    if home.stat().st_uid != os.getuid() or home.stat().st_mode & 0o077:
        parser.error("Home must be private and owned by current UID")
    import jobs

    store = Store(home / "event_monitor" / "monitors.sqlite")
    registry = jobs.Registry(home / "job_registry")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with open(home / "event_monitor" / "worker.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("An event collector already owns this store")
        worker = Worker(
            store, F1Adapters().mapping(), triage=FreeTriage(store), registry=registry
        )
        while not stop.is_set():
            worker.collect()
            if args.once:
                break
            stop.wait(60)


if __name__ == "__main__":
    main()
