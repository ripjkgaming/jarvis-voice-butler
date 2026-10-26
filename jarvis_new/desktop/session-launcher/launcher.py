#!/usr/bin/python3
"""Ordered KDE login/unlock launcher; no polling while idle and no app duplicates."""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
import kwin_bridge_loader  # noqa: E402 — shared desktop bridge helper

SERVICE = "org.jarvis.SessionLauncher"
SCRIPT = "jarvis-session-launcher"
CONFIG = Path.home() / ".config/jarvis/session-launcher.json"
AUTO = Path.home() / ".config/autostart/jarvis-session-launcher.desktop"
OLD_AUTO = Path.home() / ".config/autostart/Jarvis.desktop"
LOG = Path.home() / ".local/state/jarvis-session-launcher.log"
DEFAULT = {
    "login_delay_seconds": 8,
    "unlock_delay_seconds": 1.5,
    "window_timeout_seconds": 45,
    "apps": [
        {
            "name": "Brave",
            "desktop": "brave-browser.desktop",
            "identities": [
                "brave-browser",
                "brave-browser-stable",
                "brave",
                "com.brave.browser",
            ],
            "size": {"width": 1280, "height": 820},
        },
        {
            "name": "ChatGPT",
            "desktop": "chatgpt.desktop",
            "identities": ["chatgpt", "com.openai.chatgpt", "com.openai.codex"],
            "size": {"width": 1200, "height": 800},
        },
        {
            "name": "Jarvis",
            "desktop": "jarvis.desktop",
            "identities": ["jarvis", "jarvis-shell", "dev.jarvis.shell"],
            "title": "Jarvis",
            "size": {"width": 1280, "height": 720},
        },
    ],
}


def matches(app, window):
    identities = {
        window.get("app", "").lower().removesuffix(".desktop"),
        window.get("resourceClass", "").lower(),
    }
    return (
        bool(identities.intersection(app["identities"]))
        and (not app.get("title") or window.get("caption") == app["title"])
        and (not window.get("hidden", False) or window.get("minimized", False))
    )


def primary_output(data):
    outputs = [o for o in data["outputs"] if o.get("enabled") and o.get("connected")]
    if not outputs:
        raise RuntimeError("No enabled display")
    return min(outputs, key=lambda o: o.get("priority") or 999)["name"]


class Sequence:
    """State machine shared by real D-Bus service and deterministic tests."""

    def __init__(self, config, primary, launch, now=time.monotonic):
        self.config, self.primary, self.launch, self.now = config, primary, launch, now
        self.index, self.phase = 0, "next"
        self.deadline = 0.0
        self.quiet_since = None
        self.results = []
        self.error = None
        self.target = None

    def fail(self, reason):
        self.error = reason
        self.phase = "done"
        logging.error(reason)
        return {"stop": True}

    def step(self, snapshot, overlay_busy=False):
        now = self.now()
        if self.phase == "done":
            return {"stop": True}
        if (snapshot.get("placement") or {}).get("error"):
            return self.fail(snapshot["placement"]["error"])
        if self.index >= len(self.config["apps"]):
            self.phase = "done"
            return {"stop": True}
        app = self.config["apps"][self.index]
        windows = [w for w in snapshot["windows"] if matches(app, w)]
        busy = snapshot["busy"] or overlay_busy
        if self.phase == "next":
            if windows:
                self.results.append({"app": app["name"], "result": "already open"})
                self.index += 1
            elif not busy:
                self.launch(app)
                logging.info("Launching %s", app["name"])
                self.phase = "awaiting"
                self.deadline = now + self.config["window_timeout_seconds"]
        elif self.phase == "awaiting":
            if windows:
                self.target = windows[0]["id"]
                self.phase = "placing"
                self.deadline = now + 10
                return {
                    "place": True,
                    "id": self.target,
                    "primary": self.primary,
                    "size": app["size"],
                    "rx": random.random(),
                    "ry": random.random(),
                }
            if now >= self.deadline:
                return self.fail(
                    f"{app['name']} did not show a window; sequence stopped"
                )
        elif self.phase == "placing":
            placement = snapshot.get("placement") or {}
            window = next((w for w in windows if w["id"] == self.target), None)
            if placement.get("id") == self.target and window:
                a, g = placement["area"], window["geometry"]
                fits = (
                    g["x"] >= a["x"] - 1
                    and g["y"] >= a["y"] - 1
                    and g["x"] + g["width"] <= a["x"] + a["width"] + 1
                    and g["y"] + g["height"] <= a["y"] + a["height"] + 1
                )
                if fits:
                    self.phase = "settling"
                    self.earliest = now + 0.7
                    self.deadline = now + 30
                    self.quiet_since = None
            if now >= self.deadline:
                return self.fail(
                    f"Could not fit {app['name']} inside the display work area"
                )
        elif self.phase == "settling":
            if busy or now < self.earliest:
                self.quiet_since = None
            elif self.quiet_since is None:
                self.quiet_since = now
            elif now - self.quiet_since >= 0.4:
                self.results.append({"app": app["name"], "result": "opened and placed"})
                self.index += 1
                self.phase = "next"
            if now >= self.deadline:
                return self.fail("Animation did not become idle; sequence stopped")
        return {}


def qdbus(*args):
    return subprocess.check_output(["qdbus", *args], text=True, timeout=5).strip()


def serve(login):
    import dbus
    import dbus.service
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    if bus.name_has_owner(SERVICE):
        return
    loop = GLib.MainLoop()
    name = dbus.service.BusName(SERVICE, bus=bus, do_not_queue=True)

    class Launcher(dbus.service.Object):
        def __init__(self):
            super().__init__(name, "/Launcher")
            self.sequence = None
            self.latest = {}
            self.pending = 0
            self.inspect = False
            self.active = False
            self.started = 0
            self.last_step = 0
            self.children = []
            self.locked = True
            self.lock_changed(self.read_lock())
            bus.add_signal_receiver(
                self.lock_changed,
                signal_name="ActiveChanged",
                dbus_interface="org.freedesktop.ScreenSaver",
                bus_name="org.freedesktop.ScreenSaver",
                path="/ScreenSaver",
            )
            GLib.timeout_add_seconds(2, self.watchdog)
            if login:
                self.schedule(json.loads(CONFIG.read_text())["login_delay_seconds"])

        def read_lock(self):
            try:
                return bool(
                    bus.get_object(
                        "org.freedesktop.ScreenSaver", "/ScreenSaver"
                    ).GetActive(dbus_interface="org.freedesktop.ScreenSaver", timeout=2)
                )
            except dbus.DBusException:
                return True  # Wait for a known unlocked desktop.

        def lock_changed(self, locked):
            was_locked = self.locked
            self.locked = bool(locked)
            if self.locked:
                if self.pending:
                    GLib.source_remove(self.pending)
                    self.pending = 0
                if self.sequence and self.active:
                    self.sequence.fail(
                        "Desktop locked; resume missing apps on next unlock"
                    )
                self.stop_script()
            elif was_locked and hasattr(self, "ready"):
                self.schedule(json.loads(CONFIG.read_text())["unlock_delay_seconds"])
            self.ready = True

        def schedule(self, seconds):
            if self.pending or self.active or self.locked:
                return
            self.pending = GLib.timeout_add(int(seconds * 1000), self.begin)

        def begin(self, inspect=False):
            self.pending = 0
            if self.active or self.read_lock():
                return False
            try:
                self.inspect = inspect
                config = json.loads(CONFIG.read_text())
                primary = primary_output(
                    json.loads(
                        subprocess.check_output(
                            ["kscreen-doctor", "-j"], text=True, timeout=5
                        )
                    )
                )
                self.sequence = Sequence(config, primary, self.launch)
                self.active = True
                self.started = self.last_step = time.monotonic()
                kwin_bridge_loader.unload(SCRIPT)
                kwin_bridge_loader.load(ROOT / "bridge.js", SCRIPT)
                logging.info(
                    "Started %s on %s", "inspection" if inspect else "sequence", primary
                )
            except Exception as error:
                logging.exception("Could not start sequence")
                if self.sequence:
                    self.sequence.fail(str(error))
                self.stop_script()
            return False

        def launch(self, app):
            # Resolve the user's existing desktop entry, preserving its flags and working directory.
            self.children.append(
                subprocess.Popen(
                    ["gtk-launch", app["desktop"]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )

        def overlay_busy(self):
            service = "org.jarvis.TaskbarPuppet"
            if not bus.name_has_owner(service):
                return False
            status = json.loads(
                bus.get_object(service, "/Puppet").Status(
                    dbus_interface=service, timeout=1
                )
            )
            return status.get("visible", False) or status.get("timerActive", False)

        def watchdog(self):
            self.children = [p for p in self.children if p.poll() is None]
            if self.active and (
                time.monotonic() - self.last_step > 10
                or time.monotonic() - self.started > 240
            ):
                self.sequence.fail("Desktop bridge timed out")
                self.stop_script()
            return True

        def stop_script(self):
            self.active = False
            try:
                kwin_bridge_loader.unload(SCRIPT)
            except Exception:
                logging.exception("Could not unload launcher bridge")
            return False

        @dbus.service.method(SERVICE, in_signature="s", out_signature="s")
        def Step(self, payload):  # noqa: N802 — public D-Bus method
            self.last_step = time.monotonic()
            self.latest = json.loads(payload)
            try:
                if self.inspect or self.locked or not self.active:
                    command = {"stop": True}
                else:
                    command = self.sequence.step(self.latest, self.overlay_busy())
            except Exception as error:
                command = self.sequence.fail(str(error))
                logging.exception("Sequence failed")
            if command.get("stop"):
                logging.info(
                    "Finished: %s", self.sequence.results if self.sequence else []
                )
                GLib.idle_add(self.stop_script)
            return json.dumps(command)

        @dbus.service.method(SERVICE, out_signature="s")
        def Status(self):  # noqa: N802 — public D-Bus method
            return json.dumps(
                {
                    "active": self.active,
                    "locked": self.locked,
                    "pending": bool(self.pending),
                    "phase": self.sequence.phase if self.sequence else "idle",
                    "results": self.sequence.results if self.sequence else [],
                    "error": self.sequence.error if self.sequence else None,
                    "snapshot": self.latest,
                }
            )

        @dbus.service.method(SERVICE)
        def Run(self):  # noqa: N802 — public D-Bus method
            self.schedule(0.1)

        @dbus.service.method(SERVICE)
        def Inspect(self):  # noqa: N802 — public D-Bus method
            if not self.active and not self.pending:
                GLib.idle_add(self.begin, True)

        @dbus.service.method(SERVICE)
        def Quit(self):  # noqa: N802 — public D-Bus method
            if self.pending:
                GLib.source_remove(self.pending)
            self.stop_script()
            loop.quit()

    launcher = Launcher()
    loop.run()
    return launcher


def start(login=False):
    if SERVICE in qdbus().split():
        return
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as stream:
        subprocess.Popen(
            ["/usr/bin/python", str(Path(__file__).resolve()), "serve"]
            + (["--login"] if login else []),
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
    for _ in range(50):
        if SERVICE in qdbus().split():
            return
        time.sleep(0.1)
    raise RuntimeError(f"Launcher did not start; see {LOG}")


def install():
    backup = (
        Path.home()
        / ".local/share/jarvis-desktop-backups"
        / ("session-launcher-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    )
    backup.mkdir(parents=True)
    for path in (AUTO, OLD_AUTO, CONFIG):
        if path.exists():
            shutil.copy2(path, backup / path.name)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(DEFAULT, indent=2) + "\n")
    # Disable only the competing Jarvis entry. It remains available in the app menu.
    if OLD_AUTO.exists():
        config = configparser.ConfigParser(interpolation=None)
        config.optionxform = str
        config.read(OLD_AUTO)
        config["Desktop Entry"]["Hidden"] = "true"
        with OLD_AUTO.open("w") as stream:
            config.write(stream, space_around_delimiters=False)
    AUTO.parent.mkdir(parents=True, exist_ok=True)
    AUTO.write_text(
        "[Desktop Entry]\nType=Application\nName=Ordered desktop startup\n"
        f'Exec=/usr/bin/python "{Path(__file__).resolve()}" start --login\n'
        "OnlyShowIn=KDE;\nX-KDE-autostart-after=panel\nStartupNotify=false\n"
    )
    start()
    print(f"Installed login/unlock launcher. Backup: {backup}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["install", "start", "serve", "status", "run", "inspect", "stop"],
    )
    parser.add_argument("--login", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    if args.action == "install":
        install()
    elif args.action == "start":
        start(args.login)
    elif args.action == "serve":
        serve(args.login)
    else:
        method = {
            "run": "Run",
            "inspect": "Inspect",
            "stop": "Quit",
            "status": "Status",
        }[args.action]
        print(qdbus(SERVICE, "/Launcher", SERVICE + "." + method))
