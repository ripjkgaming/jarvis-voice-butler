#!/usr/bin/python3
"""Hide the user's windows for the JARVIS screensaver, then bring them back in a choreographed wake.

  sleep_ctl.py sleep [--pid N ...]   record every window's state, then minimise them
  sleep_ctl.py wake                  unminimise, scatter, animate back, active window last
  sleep_ctl.py list                  read-only KWin dump of the windows sleep would touch

KWin scripts cannot write files, so sleep-kwin.js runs inside the compositor and reports
over D-Bus (org.jarvis.SleepCtl). This process owns ~/.jarvis/sleep_windows.json.
Windows are only minimised or moved, never closed. --pid limits a run to those process ids.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # desktop/ holds kwin_bridge_loader.py
import kwin_bridge_loader  # noqa: E402 - shared KWin script loader

SERVICE = "org.jarvis.SleepCtl"
OBJECT = "/Sleep"
SCRIPT_NAME = "jarvis-sleep"
SCRIPT = HERE / "sleep-kwin.js"
STATE = Path(os.environ.get("JARVIS_SLEEP_STATE", Path.home() / ".jarvis" / "sleep_windows.json"))
TIMEOUT_S = 20


def _unload() -> None:
    try:
        kwin_bridge_loader.unload(SCRIPT_NAME)
    except Exception:
        pass


def _write_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE)


def _run(op: str, pids: list[int] | None = None) -> dict:
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    if bus.name_has_owner(SERVICE):
        raise RuntimeError(f"{SERVICE} is in use; another sleep/wake is running")
    name = dbus.service.BusName(SERVICE, bus=bus, do_not_queue=True)
    loop = GLib.MainLoop()
    result: dict = {}

    class Receiver(dbus.service.Object):
        def __init__(self):
            super().__init__(name, OBJECT)

        @dbus.service.method(SERVICE, in_signature="s", out_signature="s")
        def Request(self, _ready):  # noqa: N802 - D-Bus method name
            if op == "wake":
                try:
                    state = json.loads(STATE.read_text())
                except (OSError, ValueError) as error:
                    return json.dumps({"op": "error", "error": f"no usable sleep state at {STATE}: {error}"})
                return json.dumps({"op": "wake", "state": state})
            return json.dumps({"op": op, "pids": pids or []})

        @dbus.service.method(SERVICE, in_signature="s", out_signature="s")
        def Record(self, payload):  # noqa: N802
            # Refuse to overwrite an existing state: a second sleep would lose the real geometry.
            if STATE.exists():
                return "refused: sleep state already exists; run wake first"
            try:
                state = json.loads(payload)
                if not isinstance(state.get("windows"), list):
                    raise ValueError("no window list")
                _write_state(state)
            except (OSError, ValueError) as error:
                return f"refused: {error}"
            return "ok"

        @dbus.service.method(SERVICE, in_signature="s", out_signature="s")
        def Done(self, payload):  # noqa: N802
            try:
                result.update(json.loads(payload))
            except ValueError:
                result.update({"status": "error", "error": payload})
            loop.quit()
            return "ok"

    def give_up() -> bool:
        result.setdefault("status", "timeout")
        loop.quit()
        return False

    receiver = None
    GLib.timeout_add_seconds(TIMEOUT_S, give_up)
    try:
        # Registering OBJECT on the shared session connection fails while an earlier
        # run in this process still holds it, so this is created inside the try.
        receiver = Receiver()
        _unload()
        kwin_bridge_loader.load(SCRIPT, SCRIPT_NAME)
        loop.run()
    finally:
        _unload()
        # The receiver and the bus name sit in a reference cycle, so neither is freed
        # until a GC pass. Remove the object path and release the name here, or the next
        # run (and the controller's fullscreen probe) fails: "already a handler" / "in use".
        if receiver is not None:
            with contextlib.suppress(Exception):
                receiver.remove_from_connection()
        with contextlib.suppress(Exception):
            bus.release_name(SERVICE)
    result.setdefault("status", "error")
    return result


def sleep_windows(pids: list[int] | None = None) -> dict:
    """Record window state to the state file, then minimise the recorded windows."""
    return _run("sleep", pids)


def wake_windows() -> dict:
    """Restore the recorded windows. The state file is removed only after a clean wake."""
    result = _run("wake")
    if result.get("status") == "ok":
        STATE.unlink(missing_ok=True)
    return result


def list_windows(pids: list[int] | None = None) -> dict:
    """Read-only: the windows sleep would touch, with their current geometry and state."""
    return _run("list", pids)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JARVIS window sleep/wake over KWin.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sleep_p = sub.add_parser("sleep", help="record and minimise windows")
    sleep_p.add_argument("--pid", type=int, action="append", help="limit to this process id (repeatable)")
    sub.add_parser("wake", help="scatter and restore recorded windows")
    list_p = sub.add_parser("list", help="read-only KWin dump")
    list_p.add_argument("--pid", type=int, action="append", help="limit to this process id (repeatable)")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "sleep":
            result = sleep_windows(args.pid)
        elif args.cmd == "wake":
            result = wake_windows()
        else:
            result = list_windows(args.pid)
    except Exception as error:  # report, do not traceback
        print(json.dumps({"status": "error", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
