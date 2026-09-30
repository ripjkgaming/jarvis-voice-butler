"""Wayland input-idle probe. Run with the SYSTEM python3 (it has pywayland).

Binds ext_idle_notifier_v1 (KWin/Plasma supports it) and keeps a state file
current: {"idle": bool, "changed": ts, "updated": ts, "timeout_s": n}. The
compositor tells us when there has been no keyboard/mouse input for
`timeout_s` seconds and when input resumes, so nothing here reads
/dev/input and no camera is involved.

Usage: python3 input_idle_helper.py STATE_FILE [TIMEOUT_SECONDS=300]
Exits non-zero (state file removed) when the protocol is unavailable so the
parent can report "unknown" instead of guessing.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

RECENT_S = 10  # "input in the last 10 seconds" (sustained-input detection)


def _write(
    path: Path, idle: bool, changed: float, timeout_s: int, recent: bool = False
) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "idle": idle,
                "changed": changed,
                "updated": time.time(),
                "timeout_s": timeout_s,
                "recent": recent,
                "pid": os.getpid(),
            }
        )
    )
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    timeout_s = int(argv[2]) if len(argv) > 2 else 300
    try:
        from pywayland.client import Display
        from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1
        from pywayland.protocol.wayland import WlSeat
    except Exception as exc:
        print(f"pywayland unavailable: {exc}", file=sys.stderr)
        return 3

    state = {"idle": False, "changed": time.time(), "recent": True}
    globals_: dict[str, tuple[int, int]] = {}

    display = Display()
    display.connect()
    registry = display.get_registry()

    def on_global(reg, name, interface, version):
        globals_[interface] = (name, version)

    registry.dispatcher["global"] = on_global
    display.roundtrip()
    if "ext_idle_notifier_v1" not in globals_ or "wl_seat" not in globals_:
        print("compositor lacks ext_idle_notifier_v1", file=sys.stderr)
        return 4
    notifier = registry.bind(globals_["ext_idle_notifier_v1"][0], ExtIdleNotifierV1, 1)
    seat = registry.bind(globals_["wl_seat"][0], WlSeat, min(globals_["wl_seat"][1], 7))
    note = notifier.get_idle_notification(timeout_s * 1000, seat)

    def flush_state():
        _write(path, state["idle"], state["changed"], timeout_s, state["recent"])

    def idled(_n):
        state.update(idle=True, changed=time.time())
        flush_state()

    def resumed(_n):
        state.update(idle=False, changed=time.time())
        flush_state()

    note.dispatcher["idled"] = idled
    note.dispatcher["resumed"] = resumed

    # Second, short notification: is Sir actively typing/moving right now?
    recent_note = notifier.get_idle_notification(RECENT_S * 1000, seat)

    def recent_idled(_n):
        state["recent"] = False
        flush_state()

    def recent_resumed(_n):
        state["recent"] = True
        flush_state()

    recent_note.dispatcher["idled"] = recent_idled
    recent_note.dispatcher["resumed"] = recent_resumed
    flush_state()
    display.flush()

    fd = display.get_fd()
    import select

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 15.0)
            if ready:
                display.dispatch(block=True)
                display.flush()
            # Heartbeat so the reader can tell a live helper from a dead one.
            flush_state()
    except KeyboardInterrupt:
        return 0
    finally:
        with_suppress = __import__("contextlib").suppress
        with with_suppress(Exception):
            path.unlink()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
