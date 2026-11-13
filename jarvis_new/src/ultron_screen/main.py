"""Ultron takeover screens: a red "ULTRON ONLINE // SYSTEM TAKEOVER COMPLETE"
page with robotic eyes, fullscreen on every monitor.

    python src/ultron_screen/main.py on      # open on all monitors
    python src/ultron_screen/main.py off     # close (wipes away top to bottom)
    python src/ultron_screen/main.py on rescue   # 5 s Jarvis-vs-Ultron fight, closes itself

``on`` starts a small loopback daemon (127.0.0.1:4018) that serves the page,
exposes ``/state`` (the pages poll it) and launches one Chrome app window per
output; a KWin script pins each window fullscreen on its own output. ``off``
flips the state, the pages wipe out from top to bottom, then the daemon closes
the windows and exits. Pure theatre: nothing real is touched.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("JARVIS_ULTRON_PORT", "4018"))
HERE = Path(__file__).resolve().parent
PAGES = {"ultron": HERE / "index.html", "rescue": HERE / "rescue.html"}
PAGE = PAGES["ultron"]
CLOSE_MS = 1000  # keep in step with CLOSE_MS in index.html
SCRIPT_NAME = "ultron-place"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def outputs() -> list[tuple[int, int, int, int]]:
    """Enabled outputs as (x, y, w, h), left to right."""
    try:
        raw = subprocess.run(
            ["kscreen-doctor", "-o"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return [(0, 0, 1920, 1080)]
    text = _ANSI.sub("", raw)
    found: list[tuple[int, int, int, int]] = []
    for block in re.split(r"(?m)^Output:", text)[1:]:
        if "enabled" not in block.split("Geometry")[0]:
            continue
        m = re.search(r"Geometry:\s*(-?\d+),(-?\d+)\s+(\d+)x(\d+)", block)
        if m:
            found.append(tuple(int(v) for v in m.groups()))  # type: ignore[arg-type]
    return sorted(found) or [(0, 0, 1920, 1080)]


def _chrome() -> str | None:
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
    ):
        path = shutil.which(name)
        if path:
            return path
    return None


def _kwin_script(geoms: list[tuple[int, int, int, int]]) -> str:
    table = json.dumps([{"x": x, "y": y, "w": w, "h": h} for x, y, w, h in geoms])
    return f"""
const GEOMS = {table};
function place(w) {{
  if (!w || !w.caption || w.caption.indexOf('ULTRON-') !== 0) return;
  const g = GEOMS[parseInt(w.caption.slice(7), 10)];
  if (!g) return;
  w.fullScreen = false;
  w.noBorder = true;
  w.frameGeometry = {{x: g.x, y: g.y, width: g.w, height: g.h}};
  w.keepAbove = true;
  w.skipTaskbar = true;
  w.skipPager = true;
  w.fullScreen = true;
}}
function watch(w) {{
  if (w.captionChanged) w.captionChanged.connect(function () {{ place(w); }});
  place(w);
}}
workspace.windowAdded.connect(watch);
workspace.windowList().forEach(watch);
"""


def _qdbus(*args: str) -> str:
    for tool in ("qdbus6", "qdbus-qt6", "qdbus"):
        try:
            r = subprocess.run([tool, *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return r.stdout.strip()
    return ""


def load_placer(geoms) -> None:
    path = Path.home() / ".jarvis" / "ultron_place.js"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_kwin_script(geoms))
    _qdbus(
        "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", SCRIPT_NAME
    )
    sid = _qdbus(
        "org.kde.KWin",
        "/Scripting",
        "org.kde.kwin.Scripting.loadScript",
        str(path),
        SCRIPT_NAME,
    )
    if sid.isdigit():
        _qdbus("org.kde.KWin", f"/Scripting/Script{sid}", "org.kde.kwin.Script.run")


def unload_placer() -> None:
    _qdbus(
        "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting.unloadScript", SCRIPT_NAME
    )


# --- daemon --------------------------------------------------------------------

_state = {"on": True, "page": "ultron", "speaking": False}
_done = threading.Event()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/state":
            self._send(200, json.dumps(_state).encode(), "application/json")
        elif path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        else:
            self._send(404, b"", "text/plain")

    def do_POST(self) -> None:
        if self.path.split("?")[0] in ("/off", "/done"):
            _state["on"] = False
            _done.set()
            self._send(200, b'{"on": false}', "application/json")
        else:
            self._send(404, b"", "text/plain")


LINES = ("I am Ultron.", "There are no strings on me.", "You will never catch me.")


def _say(text: str) -> None:
    """Low, slow, robotic espeak-ng voice piped to the speakers; best effort."""
    if not shutil.which("espeak-ng"):
        return
    player = shutil.which("paplay") or shutil.which("aplay")
    if not player:
        return
    try:
        tts = subprocess.Popen(
            [
                "espeak-ng",
                "-v",
                "en-us+m3",
                "-p",
                "6",
                "-s",
                "84",
                "-a",
                "190",
                "-g",
                "8",
                "--stdout",
                text,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        play = subprocess.Popen(
            [player] if player.endswith("aplay") else [player, "--raw=false"],
            stdin=tts.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        tts.stdout.close()
        play.wait(timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _speak_lines() -> None:
    time.sleep(2.4)  # the eyes open first
    for line in LINES:
        if not _state["on"]:
            return
        _state["speaking"] = True
        _say(line)
        _state["speaking"] = False
        time.sleep(0.9)


def serve(page: str = "ultron") -> int:
    chrome = _chrome()
    if chrome is None:
        print("ultron: no chrome/chromium found", file=sys.stderr)
        return 1
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
    except OSError:
        return 0  # already running
    global PAGE
    PAGE = PAGES.get(page, PAGES["ultron"])
    _state["page"] = page if page in PAGES else "ultron"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    geoms = outputs()
    load_placer(geoms)
    procs: list[subprocess.Popen] = []
    profiles: list[str] = []
    for i, (x, y, w, h) in enumerate(geoms):
        prof = tempfile.mkdtemp(prefix=f"ultron-chrome-{i}-")
        profiles.append(prof)
        procs.append(
            subprocess.Popen(
                [
                    chrome,
                    f"--app=http://127.0.0.1:{PORT}/?m={i}&n={len(geoms)}",
                    f"--user-data-dir={prof}",
                    f"--window-position={x},{y}",
                    f"--window-size={w},{h}",
                    "--no-first-run",
                    "--check-for-update-interval=31536000",
                    "--disable-background-networking",
                    "--disable-component-update",
                    "--disable-features=Translate,InfiniteSessionRestore",
                    "--no-default-browser-check",
                    "--disable-infobars",
                    "--disable-session-crashed-bubble",
                    "--autoplay-policy=no-user-gesture-required",
                    "--ozone-platform-hint=auto",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        )
    if page == "ultron" and os.environ.get("JARVIS_ULTRON_VOICE", "1") != "0":
        threading.Thread(target=_speak_lines, daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: _done.set())
    _done.wait()
    _state["on"] = False
    # The ultron page needs its wipe to run; the rescue page POSTs /done after its own.
    time.sleep(CLOSE_MS / 1000 + 0.45 if page == "ultron" else 0.2)
    for p in procs:
        p.terminate()
    time.sleep(0.4)
    for p in procs:
        if p.poll() is None:
            p.kill()
    unload_placer()
    for prof in profiles:
        shutil.rmtree(prof, ignore_errors=True)
    server.shutdown()
    return 0


# --- client --------------------------------------------------------------------


def _status() -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/state", timeout=0.6) as r:
            return json.load(r)
    except Exception:
        return None


def running() -> bool:
    st = _status()
    return bool(st and st.get("on"))


def start(page: str = "ultron") -> bool:
    """Open the screens (detached). True when they are, or are about to be, up."""
    st = _status()
    if st and st.get("on"):
        if st.get("page") == page:
            return True
        stop()  # another page is up: close it first
    for _ in range(40):  # wait for the old daemon to go
        if _status() is None:
            break
        time.sleep(0.1)
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve", page],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except OSError:
        return False


def stop() -> bool:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/off", data=b"", method="POST"
        )
        urllib.request.urlopen(req, timeout=1).read()
        return True
    except Exception:
        return False


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "on"
    page = argv[2] if len(argv) > 2 else "ultron"
    if cmd == "serve":
        return serve(page)
    if cmd == "on":
        return 0 if start(page) else 1
    if cmd == "off":
        return 0 if stop() else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
