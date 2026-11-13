"""Monitor mode: Jarvis stands guard over the screens. Nothing is recorded.

Companion to watch_mode.py. Watch Mode guards the room with the webcam and
saves clips; Monitor Mode only covers every screen with a bank-vault door that
locks shut (src/monitor_ascii.py) and says Jarvis is monitoring this device.

* no camera, no clips, no screen capture, no activity log, no status history:
  the only file written is the pid file, and stdout/stderr go to /dev/null;
* same exit as Watch Mode: Ctrl+Alt+Shift+W on a card, or the KDE shortcut /
  `jarvis-watch-stop` (watch_mode.request_stop also ends this), or `monitor stop`;
* start it with `monitor` in a terminal or by voice ("monitor mode").

One instance at a time ($JARVIS_HOME/monitor/monitor.pid). Pure helpers are
importable without a display for tests.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import watch_mode as _wm

STOP_KEYBIND = _wm.STOP_KEYBIND
#: Shown nowhere on screen, same as the watch keybind.
STATUS = "L O C K E D   ::   N O T H I N G   I S   R E C O R D E D"

_DEVICE = r"(?:the |my |this )?(?:device|laptop|computer|pc|machine|screens?|monitors?|displays?)"
_START = re.compile(
    r"^(?:please )?(?:start |enter |activate |engage |turn on |switch to )?monitor mode(?: on| please)?$"
    rf"|^(?:please )?(?:start |begin |engage )?(?:monitoring|monitor|vault) {_DEVICE}(?: for me| please)?$"
    r"|^(?:please )?(?:start |engage |activate |enter |turn on )?vault mode(?: on| please)?$"
)


def enabled() -> bool:
    return os.environ.get("JARVIS_MONITOR_MODE", "1").strip() != "0"


def parse_command(text: str) -> bool:
    """Is this cleaned voice text a monitor-mode start? Pure.

    Anchored on both ends so "monitor the build" or "my monitor is flickering"
    stay with the model. There is intentionally no voice command to stop.
    """
    if not enabled():
        return False
    t = " ".join(re.sub(r"[^a-z' ]+", " ", (text or "").lower()).split())
    t = re.sub(r"^(?:can you |could you |would you |will you )", "", t)
    return bool(_START.match(t))


# --- paths / single instance ------------------------------------------------


def state_dir() -> Path:
    return _wm.jarvis_home() / "monitor"


def pid_path() -> Path:
    return state_dir() / "monitor.pid"


def stop_path() -> Path:
    return state_dir() / "stop"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True
    argv = cmd.rstrip(b"\0").split(b"\0")
    return any(arg.endswith(b"monitor_mode.py") for arg in argv)


def running_pid() -> int | None:
    try:
        pid = int(pid_path().read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


def is_running() -> bool:
    return running_pid() is not None


def request_stop() -> bool:
    """Ask a running monitor to close. True when one was running. Never raises."""
    if not is_running():
        return False
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        stop_path().write_text(str(time.time()))
    except OSError:
        return False
    return True


def launch(popen=None) -> dict:
    """Start monitor mode as a detached process. Never raises.

    Wrapped in systemd-inhibit so the machine does not sleep under the vault.
    """
    if popen is None:
        popen = subprocess.Popen
    if not enabled():
        return {
            "ok": False,
            "error": "Monitor mode is switched off (JARVIS_MONITOR_MODE=0).",
        }
    if is_running():
        return {"ok": True, "state": "already monitoring"}
    if _wm.is_running():
        return {"ok": False, "error": "Watch Mode is active. Stop it first, Sir."}
    argv = [sys.executable, str(Path(__file__).resolve())]
    if shutil.which("systemd-inhibit"):
        argv = [
            "systemd-inhibit",
            "--what=sleep:idle",
            "--who=Jarvis",
            "--why=Monitor mode is active",
            *argv,
        ]
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            stop_path().unlink()  # a stale request must not end the new session
        popen(
            argv,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "state": "monitoring"}


# --- live readouts (system facts only, nothing about the user's activity) ---


def uptime_s() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def mem_pct() -> int | None:
    try:
        kv = {}
        for line in Path("/proc/meminfo").read_text().splitlines()[:6]:
            k, v = line.split(":", 1)
            kv[k] = int(v.split()[0])
        return round(100 * (1 - kv["MemAvailable"] / kv["MemTotal"]))
    except (OSError, ValueError, KeyError):
        return None


def hms(sec: float) -> str:
    sec = int(max(0, sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def readouts(bay: int, bays: int, geom, since: float):
    """(left lines, right lines, status) for one card. Pure given the system."""
    _x, _y, w, h = geom
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = 0.0
    mem = mem_pct()
    left = (
        f"BAY {bay + 1} OF {bays}",
        f"DISPLAY {w}X{h}",
        f"HOST UP {hms(uptime_s())}",
    )
    right = (
        f"LOAD {load:.2f}",
        f"MEM {mem}%" if mem is not None else "MEM --",
        f"VAULT SINCE {time.strftime('%H:%M', time.localtime(since))}",
    )
    return left, right, STATUS


# --- screens ------------------------------------------------------------------


class MonitorScreen:
    """A vault door on every monitor. Only STOP_KEYBIND (or a stop request) closes it."""

    def __init__(self, geoms: list | None = None) -> None:
        import tkinter as tk

        self.tk = tk
        self.root = tk.Tk(className="jarvis-monitor")
        self.root.title("Jarvis Monitor")
        self.held: set[str] = set()
        self.closing = False
        self.since = time.time()
        self._stop_polled = 0.0
        self._mono = _wm._font(
            self.root, ("Adwaita Mono", "DejaVu Sans Mono"), "TkFixedFont"
        )
        self._fixed = geoms is not None
        self._monitor_at = time.time()
        self.panels: list = []
        self.geoms: list = []
        self._build(geoms if geoms is not None else _wm.monitors())
        self.root.bind_all("<KeyPress>", self._key_down)
        self.root.bind_all("<KeyRelease>", self._key_up)
        for seq in ("<Button>", "<ButtonRelease>", "<MouseWheel>", "<Motion>"):
            self.root.bind_all(seq, lambda e: "break")

    def _build(self, geoms: list) -> None:
        from monitor_ascii import VaultCard

        if not geoms:
            geoms = [
                (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
            ]
        old, fresh = self.panels, []
        try:
            for i, geom in enumerate(geoms):
                x, y, w, h = geom
                win = self.tk.Toplevel(self.root, class_="jarvis-monitor")
                win.title("Jarvis Monitor")
                win.configure(bg=_wm.BG, cursor="none")
                win.geometry(f"{w}x{h}{x:+d}{y:+d}")
                win.protocol("WM_DELETE_WINDOW", lambda: None)
                win.update_idletasks()
                win.attributes("-fullscreen", True)
                win.attributes("-topmost", True)
                c = self.tk.Canvas(
                    win,
                    width=w,
                    height=h,
                    bg=_wm.BG,
                    highlightthickness=0,
                    cursor="none",
                )
                c.pack(fill="both", expand=True)
                n = len(geoms)
                card = VaultCard(
                    c,
                    w,
                    h,
                    self._mono,
                    bay=i,
                    bays=n,
                    info=lambda i=i, n=n, g=geom: readouts(i, n, g, self.since),
                )
                fresh.append((win, c, card))
        except Exception:
            for win, _c, _card in fresh:
                with contextlib.suppress(Exception):
                    win.destroy()
            raise
        self.panels, self.geoms = fresh, list(geoms)
        self.root.withdraw()
        for win, _c, card in old:
            with contextlib.suppress(Exception):
                card.destroy()
                win.destroy()

    def _key_down(self, event) -> str:
        self.held.add(event.keysym)
        if not self.closing and _wm.is_stop_combo(event.keysym, event.state, self.held):
            self.close()
        return "break"

    def _key_up(self, event) -> str:
        self.held.discard(event.keysym)
        return "break"

    def _keep_on_top(self) -> None:
        if self.closing:
            return
        if not self._fixed and time.time() - self._monitor_at > 4.0:
            self._monitor_at = time.time()
            now_geoms = _wm.monitors()
            if now_geoms and now_geoms != self.geoms:
                with contextlib.suppress(Exception):
                    self._build(now_geoms)
        for win, _c, _card in self.panels:
            with contextlib.suppress(Exception):
                win.attributes("-topmost", True)
                win.lift()
        with contextlib.suppress(Exception):
            if self.root.focus_get() is None and self.panels:
                self.panels[0][0].focus_force()
        self.root.after(1500, self._keep_on_top)

    def _tick(self) -> None:
        if self.closing:
            return
        now = time.time()
        if now - self._stop_polled >= 0.25:
            self._stop_polled = now
            if stop_path().exists():
                with contextlib.suppress(OSError):
                    stop_path().unlink()
                self.close()
                return
        mono = time.monotonic()
        for _win, _c, card in self.panels:
            try:
                card.update(mono)
            except Exception:
                with contextlib.suppress(Exception):
                    card.destroy()
        self.root.after(33, self._tick)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        self.root.after(80, self.root.quit)

    def run(self) -> None:
        self.root.after(0, self._tick)
        self.root.after(300, self._keep_on_top)
        self.root.mainloop()
        with contextlib.suppress(Exception):
            self.root.destroy()


def main() -> int:
    if is_running() and running_pid() != os.getpid():
        return 0
    state_dir().mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        stop_path().unlink()
    pid_path().write_text(str(os.getpid()))
    try:
        MonitorScreen().run()
    finally:
        with contextlib.suppress(OSError):
            pid_path().unlink()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if sys.argv[1:2] == ["stop"]:
        raise SystemExit(0 if request_stop() else 1)
    raise SystemExit(main())
