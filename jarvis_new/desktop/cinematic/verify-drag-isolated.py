"""Stress actual KWin lifecycle in a private virtual compositor and private D-Bus.

Never uses uinput, the user's compositor, or the user's config/data/runtime dirs.
The temporary effect has a test-only command hook, absent from installed source.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent

HOOK = r'''
var probeToken="";
effect.configChanged.connect(function(){
    var r=JSON.parse(effect.readConfig("ActionRequest","{}"));
    if(!r.token||r.token===probeToken||!r.action.startsWith("probe-"))return;
    probeToken=r.token;
    var w=effects.stackingOrder.filter(function(w){return w.pid===r.pid&&w.caption===r.caption;})[0];
    if(!w)return;
    if(r.action==="probe-begin")energy.beginMove(w);
    if(r.action==="probe-finish")energy.finishMove(w);
    if(r.action==="probe-stop")energy.stop(w);
    if(r.action==="probe-geometry")energy.geometryChanged(w,w.geometry);
    if(r.action==="probe-geometry" && w.jarvisEnergy)console.warn("JARVIS_GRAVITY "+JSON.stringify({
        actual:effects.cursorPos,target:w.jarvisEnergy.gravity.to,screen:w.screen.geometry}));
    console.warn("JARVIS_PROBE "+r.token+" "+energy.active.length);
});
'''


def client():
    from PyQt6.QtCore import QEventLoop, QTimer
    from PyQt6.QtWidgets import QApplication, QLabel

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    assert os.environ.get("JARVIS_ISOLATED") == "1"
    assert os.environ["XDG_RUNTIME_DIR"].startswith("/tmp/jarvis-drag-isolated-")
    records = []

    def wait(ms):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def dbus(*args):
        return subprocess.check_output(["qdbus", "org.kde.KWin", *args], text=True, timeout=8).strip()

    def debug():
        return dbus("/Effects", "org.kde.kwin.Effects.debug", "jarvis-energy", "")

    serial = 0

    def action(w, name, **details):
        nonlocal serial
        serial += 1
        request = dict(token=str(serial), action="probe-" + name, pid=os.getpid(), caption=w.windowTitle())
        request.update(details)
        subprocess.run(["kwriteconfig6", "--file", "kwinrc", "--group", "Effect-jarvis-energy",
                        "--key", "ActionRequest", json.dumps(request)], check=True)
        dbus("/Effects", "org.kde.kwin.Effects.reconfigureEffect", "jarvis-energy")
        wait(35)

    def window(number):
        w = QLabel("Fluid particle lifecycle test")
        w.setWindowTitle("Jarvis isolated probe " + str(number))
        w.resize(640, 360)
        w.show()
        wait(100)
        return w

    wait(300)
    if dbus("/Effects", "org.kde.kwin.Effects.isEffectLoaded", "jarvis-energy") != "true":
        assert dbus("/Effects", "org.kde.kwin.Effects.loadEffect", "jarvis-energy") == "true"
    windows = [window(i) for i in range(3)]
    # Hold across two clock renewals, then complete all four release attributes.
    action(windows[0], "begin")
    assert windows[0].windowTitle() in debug(), "effect did not start"
    wait(7100)
    records.append({"case": "held-loops", "debug": debug()})
    action(windows[0], "finish")
    wait(1000)
    assert windows[0].windowTitle() not in debug(), "release leaked tracks"
    # Repeated release, resizing, rapid regrab, explicit cancellation and close.
    for i in range(36):
        w = windows[i % 3]
        action(w, "begin")
        for j in range(3):
            w.resize(580 + (i * 19 + j * 23) % 200, 320 + j * 25)
            wait(18)
            action(w, "geometry")
        action(w, "finish")
        if i % 4 == 0:
            wait(70)
            action(w, "begin")
            action(w, "finish")
        if i % 6 == 0:
            action(w, "stop")
        if i % 9 == 0:
            w.close()
            windows[i % 3] = window(10 + i)
        wait(850)
        assert "Animating window:" not in debug(), (i, debug())
    # Cross the real virtual-output boundary while dispersing and reconstructing.
    for i in range(8):
        w = windows[0]
        action(w, "begin")
        if i % 2:
            action(w, "finish")
        script = Path(os.environ["XDG_RUNTIME_DIR"]) / ("move-" + str(i) + ".js")
        script.write_text("var w=workspace.windowList().filter(function(w){return w.pid===" + str(os.getpid()) +
                          "&&w.caption===" + json.dumps(w.windowTitle()) + ";})[0];\n" +
                          "if(w){var s=workspace.screens[" + str(i % 2) + "];workspace.sendClientToScreen(w,s);" +
                          "w.frameGeometry={x:s.geometry.x+100,y:s.geometry.y+100,width:640,height:360};}")
        number = dbus("/Scripting", "org.kde.kwin.Scripting.loadScript", str(script), "probe-move-" + str(i))
        dbus("/Scripting/Script" + number, "org.kde.kwin.Script.run")
        wait(100)
        action(w, "finish")
        wait(1000)
        assert "Animating window:" not in debug(), ("output-crossing", i, debug())
    # Simultaneous windows exceed the two shader slots, then all clean up.
    for w in windows:
        action(w, "begin")
    for w in windows:
        action(w, "finish")
    wait(1000)
    assert "Animating window:" not in debug()
    for w in windows:
        w.close()
    wait(100)
    records.append({"case": "final", "debug": debug(), "cycles": 36, "output_crossings": 8, "commands": serial})
    Path(os.environ["JARVIS_PROBE_RESULT"]).write_text(json.dumps(records, indent=2))
    print("PASS: isolated KWin survived loop renewal, 36 release/resize cycles, regrab, close, cancellation and slot contention.", flush=True)


def main():
    out = ROOT / "validation"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jarvis-drag-isolated-") as temp:
        temp = Path(temp)
        for folder in ("runtime", "config", "data", "cache"):
            (temp / folder).mkdir(mode=0o700)
        package = temp / "data/kwin/effects/jarvis-energy"
        shutil.copytree(ROOT / "kwin/effects/jarvis-energy", package)
        script = package / "contents/code/main.js"
        script.write_text(script.read_text() + HOOK)
        (temp / "config/kwinrc").write_text("[KDE]\nAnimationDurationFactor=0.8\n[Plugins]\njarvis-energyEnabled=true\n[Effect-jarvis-energy]\nWindowMotionOnly=true\nParticleDragEnabled=true\nCloseEnabled=false\n")
        launcher = temp / "client.sh"
        launcher.write_text("#!/bin/sh\nexec /usr/bin/python3.14 '" + str(Path(__file__).resolve()) + "' --client\n")
        launcher.chmod(0o700)
        result = out / "drag-fluid-isolated.json"
        result.unlink(missing_ok=True)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(temp / "runtime"), XDG_CONFIG_HOME=str(temp / "config"),
                   XDG_DATA_HOME=str(temp / "data"), XDG_CACHE_HOME=str(temp / "cache"),
                   XDG_DATA_DIRS="/usr/local/share:/usr/share", XDG_CONFIG_DIRS="/etc/xdg", JARVIS_ISOLATED="1", JARVIS_PROBE_RESULT=str(result),
                   QT_FORCE_STDERR_LOGGING="1", QT_QPA_PLATFORMTHEME="generic")
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "SESSION_MANAGER"):
            env.pop(key, None)
        command = ["dbus-run-session", "--", "kwin_wayland", "--virtual", "--width", "1280", "--height", "720", "--output-count", "2",
                   "--no-lockscreen", "--no-global-shortcuts", "--no-kactivities", "--inputmethod", "/bin/true", "--socket", "jarvis-isolated",
                   "--exit-with-session", str(launcher)]
        with (out / "drag-fluid-isolated.log").open("w") as log:
            run = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=150)
        assert run.returncode == 0 and result.exists(), (run.returncode, "See validation/drag-fluid-isolated.log")
        markers=[json.loads(line.split("JARVIS_GRAVITY ",1)[1]) for line in (out/"drag-fluid-isolated.log").read_text().splitlines() if "JARVIS_GRAVITY " in line]
        assert len(markers)>=36,len(markers)
        for marker in markers:
            point,screen=marker["actual"],marker["screen"]
            expected={"x":max(screen["x"],min(screen["x"]+screen["width"],point["x"])),
                      "y":max(screen["y"],min(screen["y"]+screen["height"],point["y"]))}
            assert marker["target"]==expected,marker
        print("PASS: native cursor coordinates reach the gravity target on the owning output.")
        print(result.read_text())


if __name__ == "__main__":
    if "--client" in sys.argv:
        client()
    else:
        main()
