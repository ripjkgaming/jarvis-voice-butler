"""Private request/reply Wayland idle probe. Start only after reviewed release.

Uses the existing system-pywayland dependency, no input devices or input content.
Protocol: host sends integer sequence newline; reply is sequence idle_seconds.
A compositor roundtrip drains pending activity before each reply. One-second
notification plus monotonic duration reports confirmed idle. v1 respects idle
inhibitors (conservative); v2 input-only API may be selected in a future release.
"""

import os
import select
import sys
import time


def main():
    from pywayland.client import Display
    from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1
    from pywayland.protocol.wayland import WlSeat

    display = Display()
    display.connect()
    registry = display.get_registry()
    globals_ = {}
    registry.dispatcher["global"] = lambda reg, name, interface, version: (
        globals_.update({interface: (name, version)})
    )
    display.roundtrip()
    if "ext_idle_notifier_v1" not in globals_ or "wl_seat" not in globals_:
        return 4
    notifier = registry.bind(globals_["ext_idle_notifier_v1"][0], ExtIdleNotifierV1, 1)
    seat = registry.bind(globals_["wl_seat"][0], WlSeat, min(globals_["wl_seat"][1], 7))
    note = notifier.get_idle_notification(1000, seat)
    state = {"since": None}
    note.dispatcher["idled"] = lambda n: state.update(since=time.monotonic() - 1)
    note.dispatcher["resumed"] = lambda n: state.update(since=None)
    display.flush()
    fd = display.get_fd()
    while True:
        ready, _, _ = select.select([fd, sys.stdin], [], [], 5)
        if fd in ready:
            display.dispatch(block=True)
            display.flush()
        if sys.stdin in ready:
            line = sys.stdin.readline(80)
            if not line:
                return 0
            if not line.strip().isdigit():
                return 2
            display.roundtrip()
            since = state["since"]
            idle = 0.0 if since is None else max(0.0, time.monotonic() - since)
            print(f"{int(line)} {idle:.6f}", flush=True)


if __name__ == "__main__":
    try:
        result = main()
    except BaseException:
        result = 1
    # Avoid known pywayland finalizer crash after display teardown.
    os._exit(result)
