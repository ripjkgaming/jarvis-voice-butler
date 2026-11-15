"""Live 3D model session: versions, fast Blender edits, Codex edits, viewer state.

One live Blender (blender-mcp addon, socket 127.0.0.1:9876) holds the model.
Each project lives in ~/jarvis-projects/3d/<slug>/ with versioned snapshots
(versions/vN.blend + vN.glb) and versions.json {head, versions[]} so
"undo"/"redo" work. The HUD viewer (frontend model-viewer.tsx) polls
GET /model3d/state and loads GET /model3d/glb; both are fed from the state
file ~/.jarvis/model3d.json written here. No cursor: all voice-driven.
"""

from __future__ import annotations

import json
import math
import os
import signal
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

ROOT_REPO = Path(__file__).resolve().parent.parent
ROOT = Path(
    os.environ.get("JARVIS_3D_DIR", "") or Path.home() / "jarvis-projects" / "3d"
)
HOST, PORT = "127.0.0.1", int(os.environ.get("BLENDER_PORT", "9876"))
CODEX = os.environ.get("CODEX_BIN", "/usr/lib/chatgpt/resources/codex")
EDIT_TIMEOUT_S = 600


def _home() -> Path:
    h = os.environ.get("JARVIS_HOME", "").strip()
    return Path(h) if h else Path.home() / ".jarvis"


def state_path() -> Path:
    return _home() / "model3d.json"


# ---------------------------------------------------------------- blender


def blender_up() -> bool:
    try:
        socket.create_connection((HOST, PORT), 2).close()
        return True
    except OSError:
        return False


def ensure_blender() -> bool:
    if blender_up():
        return True
    up = Path.home() / "deck/bin/jarvis-blender-up"
    if up.exists():
        subprocess.run([str(up)], capture_output=True, timeout=90)
    return blender_up()


def blender(cmd_type: str, params: dict | None = None, timeout: float = 60) -> dict:
    """One command to the blender-mcp addon socket; raises RuntimeError on error."""
    with socket.create_connection((HOST, PORT), 5) as s:
        s.settimeout(timeout)
        s.sendall(json.dumps({"type": cmd_type, "params": params or {}}).encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            try:
                reply = json.loads(buf)
                break
            except ValueError:
                continue
    if reply.get("status") != "success":
        raise RuntimeError(str(reply.get("message", reply))[:400])
    return reply.get("result") or {}


def run_py(code: str, timeout: float = 60) -> str:
    return str(blender("execute_code", {"code": code}, timeout).get("result", ""))


# ---------------------------------------------------------------- projects


def newest_project() -> Path | None:
    best, best_t = None, 0.0
    if not ROOT.is_dir():
        return None
    for d in ROOT.iterdir():
        g = d / f"{d.name}.glb"
        if not g.is_file():
            g = d / "recipe.json"
        if g.is_file() and g.stat().st_mtime > best_t:
            best, best_t = d, g.stat().st_mtime
    return best


def current_slug() -> str | None:
    try:
        slug = json.loads(state_path().read_text()).get("slug")
        if slug and (ROOT / slug).is_dir():
            return slug
    except (OSError, ValueError):
        pass
    p = newest_project()
    return p.name if p else None


def _vpath(slug: str) -> Path:
    return ROOT / slug / "versions.json"


def load_versions(slug: str) -> dict:
    try:
        return json.loads(_vpath(slug).read_text())
    except (OSError, ValueError):
        return {"head": 0, "versions": []}


def _save_versions(slug: str, data: dict) -> None:
    _vpath(slug).write_text(json.dumps(data, indent=1))


def _export_glb(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = run_py(
        "import bpy\n"
        f"bpy.ops.export_scene.gltf(filepath={str(path)!r}, export_format='GLB', use_selection=False, export_extras=True)\n"
        "print('ok')",
        timeout=120,
    )
    if "ok" not in out or not path.is_file():
        raise RuntimeError("glb export failed")


def snapshot(slug: str, label: str) -> int:
    """Save live scene as new version vN (blend copy + glb); make it head and
    refresh the main <slug>.glb the viewer serves. Drops any redo tail."""
    data = load_versions(slug)
    keep = data["versions"][: data["head"]]
    n = (keep[-1]["n"] + 1) if keep else 1
    vdir = ROOT / slug / "versions"
    vdir.mkdir(parents=True, exist_ok=True)
    blend, glb = vdir / f"v{n}.blend", vdir / f"v{n}.glb"
    run_py(
        "import bpy\n"
        f"bpy.ops.wm.save_as_mainfile(filepath={str(blend)!r}, copy=True)\nprint('ok')",
        timeout=120,
    )
    _export_glb(glb)
    keep.append(
        {
            "n": n,
            "label": label,
            "ts": time.time(),
            "blend": str(blend),
            "glb": str(glb),
        }
    )
    data = {"head": len(keep), "versions": keep}
    _save_versions(slug, data)
    _publish(slug, glb, n)
    return n


def _publish(slug: str, glb: Path, n: int) -> None:
    shutil.copyfile(glb, ROOT / slug / f"{slug}.glb")
    set_state(slug=slug, version=n, glb_ts=time.time())


def ensure_baseline(slug: str) -> None:
    if not load_versions(slug)["versions"]:
        snapshot(slug, "original")


def goto(slug: str, index: int) -> dict:
    """Restore version at list index (0-based) into the live scene."""
    data = load_versions(slug)
    v = data["versions"][index]
    code = f"""
import bpy
src = {v["blend"]!r}
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
with bpy.data.libraries.load(src, link=False) as (df, dt):
    dt.objects = list(df.objects)
sc = bpy.context.scene
for o in dt.objects:
    if o is not None:
        sc.collection.objects.link(o)
print('ok', len(dt.objects))
"""
    run_py(code, timeout=120)
    data["head"] = index + 1
    _save_versions(slug, data)
    _publish(slug, Path(v["glb"]), v["n"])
    frame_view()
    return v


def undo(slug: str) -> str:
    data = load_versions(slug)
    if data["head"] <= 1:
        return "Nothing to undo, Sir."
    v = goto(slug, data["head"] - 2)
    return f"Undone. Back to version {v['n']}."


def redo(slug: str) -> str:
    data = load_versions(slug)
    if data["head"] >= len(data["versions"]):
        return "Nothing to redo, Sir."
    v = goto(slug, data["head"])
    return f"Redone. Version {v['n']}."


# ---------------------------------------------------------------- viewport


def frame_view() -> None:
    """Select all scene meshes and view_selected in the live GUI viewport."""
    code = """
import bpy
objs = [o for o in bpy.context.scene.objects if o.type == 'MESH']
for o in bpy.context.scene.objects:
    o.select_set(False)
for o in objs:
    o.select_set(True)
if objs:
    bpy.context.view_layer.objects.active = objs[0]
done = False
for w in bpy.context.window_manager.windows:
    for a in w.screen.areas:
        if a.type == 'VIEW_3D':
            r = next((r for r in a.regions if r.type == 'WINDOW'), None)
            with bpy.context.temp_override(window=w, area=a, region=r):
                bpy.ops.view3d.view_selected()
            a.spaces[0].shading.type = 'MATERIAL'
            done = True
print('framed' if done else 'noview')
"""
    try:
        run_py(code)
    except Exception:
        pass


def raise_window() -> None:
    """Best effort: bring the Blender window to front (X11/KWin)."""
    for cmd in (
        ["wmctrl", "-a", "Blender"],
        ["xdotool", "search", "--name", "Blender", "windowactivate"],
    ):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, capture_output=True, timeout=5)
            return


_MIN_JS = """
for (const w of workspace.windowList()) {
  if (String(w.resourceClass).toLowerCase().indexOf("blender") >= 0 || String(w.caption).indexOf("Blender") >= 0) { w.minimized = true; }
}
"""


def minimize_blender() -> None:
    """Hide the Blender window (KWin script) while the hologram stage is up. Best effort, never raises."""
    try:
        import tempfile

        qd = shutil.which("qdbus") or shutil.which("qdbus6")
        if not qd:
            return
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(_MIN_JS)
        name = "jarvis-min-blender"
        subprocess.run(
            [
                qd,
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.unloadScript",
                name,
            ],
            capture_output=True,
            timeout=5,
        )
        r = subprocess.run(
            [
                qd,
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.loadScript",
                f.name,
                name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        sid = r.stdout.strip()
        if sid.isdigit():
            subprocess.run(
                [
                    qd,
                    "org.kde.KWin",
                    f"/Scripting/Script{sid}",
                    "org.kde.kwin.Script.run",
                ],
                capture_output=True,
                timeout=5,
            )
            subprocess.run(
                [
                    qd,
                    "org.kde.KWin",
                    "/Scripting",
                    "org.kde.kwin.Scripting.unloadScript",
                    name,
                ],
                capture_output=True,
                timeout=5,
            )
        os.unlink(f.name)
    except Exception:
        pass


def close_blender() -> None:
    """Quit Blender once generation is done; the hologram is the only UI. Best effort.
    Edits relaunch it and reload the current version (see ensure_scene)."""
    try:
        pids = subprocess.run(
            ["pgrep", "-x", "blender"], capture_output=True, text=True, timeout=5
        ).stdout.split()
        for pid in pids:
            os.kill(int(pid), signal.SIGTERM)
    except Exception:
        pass


def ensure_scene(slug: str) -> None:
    """After Blender was closed and relaunched the scene is empty: reload the head version."""
    try:
        n = run_py("import bpy\nprint(len(bpy.data.objects))").strip().split()[-1]
        if n == "0":
            data = load_versions(slug)
            if data.get("versions"):
                goto(slug, max(0, min(int(data.get("head") or 1), len(data["versions"])) - 1))
    except Exception:
        pass


def open_in_blender(glb_or_blend: str | None = None) -> str:
    """Auto-open after make3d: baseline snapshot, frame, raise window."""
    if not ensure_blender():
        return "Blender not reachable"
    slug = current_slug()
    if not slug:
        return "no project"
    ensure_baseline(slug)
    frame_view()
    minimize_blender()  # the hologram stage is the UI now; Blender stays running in the background
    return f"opened {slug} in Blender (minimised)"


def screenshot(path: str, size: int = 1000) -> str:
    blender(
        "get_viewport_screenshot", {"max_size": size, "filepath": path, "format": "png"}
    )
    return path


# ---------------------------------------------------------------- viewer state

DEFAULTS = {
    "open": False,
    "rotate": True,
    "speed": 1.0,
    "dir": 1,
    "pitch": 0.0,
    "yaw": 0.0,
    "roll": 0.0,
    "spin_axis": "y",
    "sim_start": 0.0,
    "zoom": 1.0,
    "wire": True,
    "explode": False,
    "scan": False,
    "rot4d": True,
    "plane4d": "all",
    "fx": {},
    "fx_slug": "",
    "cmd": "",
    "cmd_seq": 0,
    "seq": 0,
}


def get_state() -> dict:
    try:
        s = json.loads(state_path().read_text())
    except (OSError, ValueError):
        s = {}
    out = {**DEFAULTS, **s}
    if not out.get("slug"):
        out["slug"] = current_slug()
    return out


_SHELL_BINS = (
    ROOT_REPO / "shell/src-tauri/target/release/jarvis-shell",
    ROOT_REPO / "shell/src-tauri/target/debug/jarvis-shell",
)


def _place_holo_window() -> None:
    """Fullscreen the hologram window on the least-occupied monitor (not the HUD's)."""
    try:
        import json as _json

        from system import monitor_window_move as mwm, window_layout

        screens = mwm.monitors()
        if not screens:
            return
        name = mwm.free_screen(screens)["name"]
        js = (
            "var n=%s;var o=(workspace.screens||[]).find(function(v){return v.name===n;});"
            "workspace.windowList().forEach(function(w){"
            "if(String(w.caption||'').indexOf('Hologram')<0||String(w.resourceClass||'').toLowerCase().indexOf('jarvis')<0)return;"
            "try{w.fullScreen=false;}catch(e){}"
            "if(o)try{workspace.sendClientToScreen(w,o);}catch(e){}"
            "try{w.fullScreen=true;}catch(e){}});"
        ) % _json.dumps(name)
        time.sleep(0.4)
        window_layout.run_script(js, name="jarvis-holo-place")
    except Exception:
        pass


def _holo_window(verb: str, delay: float = 0.0) -> None:
    """Show/hide the separate hologram window via the shell's CLI verbs (fail-soft)."""
    exe = next((str(b) for b in _SHELL_BINS if b.is_file()), None)
    if not exe:
        return

    def run() -> None:
        try:
            if delay:
                time.sleep(delay)
                if verb == "holohide" and get_state().get("open"):
                    return  # re-opened during the de-rez
            subprocess.run([exe, verb], capture_output=True, timeout=10)
            if verb == "holoshow":
                _place_holo_window()
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def set_state(**kw) -> dict:
    s = get_state()
    was_open = bool(s.get("open"))
    s.update(kw)
    # PROJECT always (re)shows the window: `open` can be stale-True while the window is hidden
    if "open" in kw and (bool(kw["open"]) != was_open or kw.get("cmd") == "PROJECT"):
        if kw["open"]:
            _holo_window("holoshow")
        else:
            _holo_window("holohide", 3.6)
    s["seq"] = int(s.get("seq") or 0) + 1
    s["ts"] = time.time()
    state_path().parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(s))
    tmp.replace(state_path())
    return s


def glb_path(slug: str | None = None) -> Path | None:
    slug = slug or get_state().get("slug") or current_slug()
    if not slug:
        return None
    p = ROOT / slug / f"{slug}.glb"
    if p.is_file():
        return p
    try:  # recipe-only slug: build the GLB on demand into the cache
        import recipes

        return recipes.materialize(slug)
    except Exception:
        return None


def shape4d_path(slug: str | None = None) -> Path | None:
    slug = slug or get_state().get("slug") or current_slug()
    p = ROOT / slug / "shape4d.json" if slug else None
    if p and p.is_file():
        return p
    try:  # recipe-only 4D slug
        import recipes

        return recipes.materialize4d(slug) if slug else None
    except Exception:
        return None


SIM_MORPH_S = 1.8  # stage disc -> grid morph, ends when the simulation's frame 0 plays
SIM_LEAD_S = (
    0.3 + SIM_MORPH_S
)  # sim_start = now + this: the morph starts with the speech


def sim_path(slug: str | None = None) -> Path | None:
    """Play file of a recipe-only simulation slug ('sim-<job>'): ~/.jarvis/sims/<job>.play.json."""
    slug = slug or get_state().get("slug")
    if not slug or not str(slug).startswith("sim-"):
        return None
    p = Path.home() / ".jarvis" / "sims" / f"{str(slug)[4:]}.play.json"
    return p if p.is_file() else None


def state_payload() -> dict:
    s = get_state()
    g = glb_path(s.get("slug"))
    sh = shape4d_path(s.get("slug"))
    sm = sim_path(s.get("slug"))
    s["sim"] = bool(sm and not g)
    s["sim_mtime"] = sm.stat().st_mtime if sm else 0
    s["shape4d"] = bool(sh and not g)
    s["shape4d_mtime"] = sh.stat().st_mtime if sh else 0
    s["has_glb"] = bool(g) or bool(sh) or bool(sm)
    s["glb_mtime"] = g.stat().st_mtime if g else 0
    v = load_versions(s["slug"]) if s.get("slug") else {"head": 0, "versions": []}
    s["version"] = v["versions"][v["head"] - 1]["n"] if v["head"] else 0
    s["version_count"] = len(v["versions"])
    if s.get("fx_slug") != s.get("slug"):
        s["fx"] = {}
    try:
        import model3d_parts

        s["caps"] = [
            {k: c[k] for k in ("id", "label", "role", "parts", "actions")}
            for c in model3d_parts.capabilities(g)
        ]
        s["parts"] = [
            {k: p[k] for k in ("id", "role", "extras")}
            for p in model3d_parts.parts_of(g)
        ]
    except Exception:
        s["caps"], s["parts"] = [], []
    s["movable"] = movable_parts(g)
    return s


def movable_parts(g) -> list[str]:
    """Part ids that may move/light up: named in commands.json steps (not reset steps) + their
    Blender wire companions. The HUD merges every other part into one static mesh."""
    try:
        import model3d_cmds as mcmd
        import model3d_parts as mp

        parts = mp.parts_of(g)
        out: set[str] = set()
        role = {p["id"]: p["role"] for p in parts}
        by = {mp._sanitize(p["name"]).lower(): p["id"] for p in parts}
        for c in mcmd.load(g):
            for st in c["steps"]:
                act = str(st.get("action")).replace(" ", "_")
                if act == "reset":
                    continue
                for n in st["part"] if isinstance(st["part"], list) else [st["part"]]:
                    pid = mp._sanitize(str(n))
                    # on/off/dim only light up lights; "base off" in a power-down macro moves nothing
                    if (
                        act in ("on", "off", "dim", "brighten")
                        and role.get(by.get(pid.lower(), pid)) != "light"
                    ):
                        continue
                    out.add(pid)
        out |= {by[k] for k in (x.lower() for x in out) if k in by}
        return sorted(out)
    except Exception:
        return []


def _cmd(name: str, **kw) -> dict:
    """set_state + stamp the last heard command so the hologram can react to it."""
    return set_state(cmd=name, cmd_seq=int(get_state().get("cmd_seq") or 0) + 1, **kw)


VIEW_COMMANDS = (
    "show / project / open, close / dismiss / hide, rotate / spin, stop / hold, "
    "faster, slower, reverse, zoom in, zoom out, reset, wireframe, solid, "
    "tilt up / tilt down (pitch), turn left / turn right (yaw), roll left / roll right, level, "
    "spin vertically / spin sideways / spin like a wheel, "
    "explode, assemble, scan, rebuild (replays the build-up), and for 4D shapes: "
    "rotate through the fourth dimension, stop 4D, plane xw / yw / zw / all"
)


def view_command(action: str, value: float | None = None) -> str:
    """view_command_impl + stamp what was heard and the reply for the HUD caption."""
    r = _view_command(action, value)
    try:
        set_state(
            heard=str(action or "").replace("_", " "), reply=r, say_ts=time.time()
        )
    except Exception:
        pass
    return r


def _view_command(action: str, value: float | None = None) -> str:
    """Hologram voice control. action: open|close|rotate|stop|faster|slower|reverse|
    zoom_in|zoom_out|reset|wireframe|solid|explode|assemble|scan|rebuild (+ aliases).
    Returns the spoken confirmation."""
    a = action.strip().lower().replace(" ", "_").replace("-", "_")
    s = get_state()
    if a in ("open", "show", "project", "display", "hologram"):
        if not glb_path() and not shape4d_path() and not sim_path():
            return "No 3D model to show yet, Sir."
        _cmd("PROJECT", open=True)
        return "Projecting."
    if a in ("close", "dismiss", "hide", "end", "deactivate", "clear"):
        _cmd("DEREZ", open=False, explode=False, scan=False)
        return "Hologram dismissed."
    if a in ("rotate", "spin", "start"):
        _cmd("ORBIT", rotate=True, open=True)
        return "Rotating."
    if a in ("stop", "pause", "freeze", "hold", "stop_rotating"):
        _cmd("HOLD", rotate=False)
        return "Holding."
    if a in ("faster", "speed_up"):
        _cmd("FASTER", speed=min(8.0, s["speed"] * 1.6))
        return "Faster."
    if a in ("slower", "slow_down"):
        _cmd("SLOWER", speed=max(0.15, s["speed"] / 1.6))
        return "Slower."
    if a in ("reverse", "other_way", "flip"):
        _cmd("REVERSE", dir=-s["dir"], rotate=True)
        return "Reversed."
    if a in ("zoom_in", "closer", "bigger"):
        _cmd("ZOOM +", zoom=min(4.0, s["zoom"] * (value or 1.3)))
        return "Zoomed in."
    if a in ("zoom_out", "farther", "smaller"):
        _cmd("ZOOM -", zoom=max(0.3, s["zoom"] / (value or 1.3)))
        return "Zoomed out."
    _step = float(value) if value else 30.0
    _rot = {
        "tilt_up": ("pitch", -1),
        "pitch_up": ("pitch", -1),
        "tilt_forward": ("pitch", 1),
        "tilt_down": ("pitch", 1),
        "pitch_down": ("pitch", 1),
        "tilt_back": ("pitch", -1),
        "turn_left": ("yaw", 1),
        "yaw_left": ("yaw", 1),
        "turn_right": ("yaw", -1),
        "yaw_right": ("yaw", -1),
        "rotate_left": ("yaw", 1),
        "rotate_right": ("yaw", -1),
        "roll_left": ("roll", 1),
        "roll_right": ("roll", -1),
        "roll_clockwise": ("roll", -1),
        "roll_anticlockwise": ("roll", 1),
        "roll_counterclockwise": ("roll", 1),
    }
    if a in _rot:
        ax, sg = _rot[a]
        _cmd(
            a.replace("_", " ").upper(),
            **{ax: float(s.get(ax) or 0.0) + sg * _step},
            open=True,
        )
        return {"pitch": "Tilting.", "yaw": "Turning.", "roll": "Rolling."}[ax]
    if a in ("level", "level_out", "reset_rotation", "reset_orientation", "upright"):
        _cmd("LEVEL", pitch=0.0, yaw=0.0, roll=0.0)
        return "Level."
    if a in (
        "spin_x",
        "spin_vertically",
        "spin_up_down",
        "spin_pitch",
        "spin_y",
        "spin_sideways",
        "spin_horizontally",
        "spin_yaw",
        "spin_z",
        "spin_roll",
        "spin_like_a_wheel",
    ):
        ax = (
            "x"
            if a in ("spin_x", "spin_vertically", "spin_up_down", "spin_pitch")
            else "z"
            if a in ("spin_z", "spin_roll", "spin_like_a_wheel")
            else "y"
        )
        _cmd(f"SPIN {ax.upper()}", spin_axis=ax, rotate=True, open=True)
        return f"Spinning about the {ax} axis."
    if a in (
        "replay",
        "play_again",
        "again",
        "restart_simulation",
        "replay_simulation",
        "rebuild",
    ) and str(s.get("slug") or "").startswith("sim-"):
        if not sim_path():
            return "There is no simulation to replay, Sir."
        _cmd("REPLAY", sim_start=time.time() + SIM_LEAD_S, open=True)
        return "Replaying the simulation."
    if a in ("reset", "default"):
        _cmd(
            "RESET",
            zoom=1.0,
            speed=1.0,
            dir=1,
            rotate=True,
            explode=False,
            scan=False,
            pitch=0.0,
            yaw=0.0,
            roll=0.0,
            spin_axis="y",
        )
        return "Reset."
    if a in ("wire", "wireframe", "show_wireframe", "xray", "x_ray"):
        _cmd("WIREFRAME", wire=True)
        return "Wireframe."
    if a in ("solid", "shaded", "show_solid"):
        _cmd("SOLID", wire=False)
        return "Solid."
    if a in ("explode", "explode_view", "exploded_view", "take_apart", "disassemble"):
        _cmd("EXPLODE", explode=True, open=True)
        return "Exploding the view."
    if a in ("assemble", "implode", "collapse", "put_back", "reassemble", "unexplode"):
        _cmd("ASSEMBLE", explode=False)
        return "Reassembling."
    if a in ("scan", "scan_it", "analyze", "analyse"):
        _cmd("SCAN", scan=True, open=True)
        return "Scanning."
    if a in ("stop_scan", "end_scan", "scan_off"):
        _cmd("SCAN OFF", scan=False)
        return "Scan complete."
    if a in (
        "rotate_4d",
        "4d",
        "start_4d",
        "fourth_dimension",
        "hyperrotate",
        "through_the_fourth_dimension",
        "spin_4d",
    ):
        _cmd("4D ROTATE", rot4d=True, open=True)
        return "Rotating through the fourth dimension."
    if a in ("stop_4d", "freeze_4d", "hold_4d", "stop_fourth_dimension"):
        _cmd("4D HOLD", rot4d=False)
        return "Holding the fourth-dimension rotation."
    if a in (
        "plane_xw",
        "xw",
        "plane_yw",
        "yw",
        "plane_zw",
        "zw",
        "plane_all",
        "all_planes",
        "plane_xy_zw",
        "double_rotation",
    ):
        pl = {
            "all_planes": "all",
            "plane_all": "all",
            "double_rotation": "double",
            "plane_xy_zw": "double",
        }.get(a, a.replace("plane_", ""))
        _cmd(f"4D {pl.upper()}", plane4d=pl, rot4d=True, open=True)
        return f"Rotating in the {pl.upper()} plane."
    if a in ("rebuild", "replay", "again", "rematerialize", "rematerialise"):
        _cmd("REBUILD", open=True)
        return "Rebuilding."
    return f"Hologram commands: {VIEW_COMMANDS}."


def part_command(text: str) -> str:
    """Object-specific motion by voice: "turn the lamp off", "open the left door",
    "spin the fan", "raise the arm", "make the headlights red", "what can I control"."""
    import model3d_parts as mp

    g = glb_path()
    if not g:
        return "No 3D model to control yet, Sir."
    parts = mp.parts_of(g)
    caps = mp.capabilities(g)
    t = (text or "").strip()
    reply = _part_command(g, mp, parts, caps, t)
    try:
        set_state(heard=t, reply=reply, say_ts=time.time())
    except Exception:
        pass
    return reply


def _part_command(g, mp, parts, caps, t: str) -> str:
    import model3d_cmds as mcmd  # commands.json written by the model that built this object

    if re.search(
        r"\b(what|which)\b.*\b(control|move|do|say)\b|\bwhat can (i|you)\b|\bcontrols?\b|\bwhat moves\b",
        t.lower(),
    ):
        return mcmd.list_phrases(g, 8) or mp.describe(caps)
    own = mcmd.match(g, t)
    if own:
        st = get_state()
        fx0 = (st.get("fx") if st.get("fx_slug") == st.get("slug") else {}) or {}
        fx, reply = mcmd.run(g, own, fx0, t)
        _cmd(
            "PART " + own["say"][0].upper()[:24],
            fx=fx,
            fx_slug=st.get("slug") or "",
            open=True,
        )
        return reply
    act = mp.parse_action(t)
    if not act and any(c in t.lower() for c in mp.COLORS):
        act = "on"
    if not act:
        return "Say what to do with a part, Sir: " + mp.describe(caps)
    hit = mp.resolve_target(t, caps)
    if not hit:
        want = {
            "on": "light",
            "off": "light",
            "dim": "light",
            "brighten": "light",
            "open": "hinge",
            "close": "hinge",
            "spin": "spin",
            "faster": "spin",
            "slower": "spin",
            "reverse": "spin",
            "extend": "slide",
            "retract": "slide",
        }.get(act)
        hit = [c for c in caps if want and c["role"] == want] or (
            [caps[0]] if len(caps) == 1 else []
        )
    if not hit:
        return "I could not tell which part you mean, Sir. " + mp.describe(caps)
    fx = (
        get_state().get("fx")
        if get_state().get("fx_slug") == get_state().get("slug")
        else {}
    ) or {}
    reply = ""
    for cap in hit:
        ids = mp.side_filter(t, cap, parts)
        fx, reply = mp.apply(fx, cap, ids, act, t)
    _cmd(
        "PART " + act.upper().replace("_", " "),
        fx=fx,
        fx_slug=get_state().get("slug") or "",
        open=True,
    )
    return reply or "Done."


# ---------------------------------------------------------------- fast edits

COLORS = {
    "red": (0.9, 0.05, 0.05),
    "green": (0.1, 0.7, 0.15),
    "blue": (0.1, 0.25, 0.9),
    "yellow": (0.95, 0.85, 0.1),
    "orange": (1.0, 0.45, 0.05),
    "purple": (0.5, 0.15, 0.8),
    "pink": (1.0, 0.4, 0.7),
    "white": (0.95, 0.95, 0.95),
    "black": (0.02, 0.02, 0.02),
    "grey": (0.5, 0.5, 0.5),
    "gray": (0.5, 0.5, 0.5),
    "brown": (0.4, 0.2, 0.08),
    "cyan": (0.0, 0.8, 0.9),
    "gold": (0.9, 0.7, 0.1),
    "silver": (0.75, 0.75, 0.78),
}

ROOT_CODE = """
import bpy
sc = bpy.context.scene
objs = [o for o in sc.objects if o.type not in ('CAMERA', 'LIGHT')]
roots = [o for o in objs if o.parent is None]
"""

PART_WORDS = r"\b(shade|arm|arms|leg|legs|base|head|handle|top|bottom|lid|door|wheel|wheels|part|eyes?|stripe|neck|stand|bulb|screen|seat|back|body)\b"
_NUM = r"(\d+(?:\.\d+)?)"


def _pct(text: str) -> float | None:
    m = re.search(_NUM + r"\s*(?:%|percent)", text)
    return float(m.group(1)) if m else None


def _axis(text: str) -> str:
    m = re.search(r"\b([xyz])[- ]?axis\b|\baround ([xyz])\b", text)
    return (m.group(1) or m.group(2)).upper() if m else "Z"


def fast_edit(instruction: str, slug: str) -> str | None:
    """Handle trivial whole-object commands without an LLM. Returns spoken
    text, or None when the instruction needs Codex."""
    t = " ".join(instruction.lower().replace(",", " ").split())
    t = re.sub(r"^(please |jarvis |can you |could you )+", "", t).strip(" .")

    if re.fullmatch(r"(undo|undo that|undo it|go back|revert|undo last)( change)?", t):
        return undo(slug)
    if re.fullmatch(r"redo|redo that|redo it", t):
        return redo(slug)
    if re.fullmatch(r"(save|save it|save the model|save blend|save the blend)", t):
        run_py(
            "import bpy\nbpy.ops.wm.save_as_mainfile(filepath=%r)\nprint('ok')"
            % str(ROOT / slug / f"{slug}.blend")
        )
        return "Saved."
    if re.fullmatch(
        r"(export|re-?export|export it|export glb|export the model|export as glb)", t
    ):
        _export_glb(ROOT / slug / f"{slug}.glb")
        set_state(glb_ts=time.time())
        return "Exported."
    if len(t.split()) > 9 or " and " in t or re.search(PART_WORDS, t):
        return None

    code, say = None, ""
    if re.search(r"\b(rotate|spin|turn)\b", t):
        dm = re.search(_NUM + r"\s*(?:deg|°)", t)
        deg = float(dm.group(1)) if dm else 90.0
        if re.search(r"\b(right|clockwise)\b", t):
            deg = -deg
        ax = {"X": 0, "Y": 1, "Z": 2}[_axis(t)]
        code = ROOT_CODE + (
            f"for o in roots:\n    o.rotation_mode='XYZ'\n    e=list(o.rotation_euler)\n"
            f"    e[{ax}]+={math.radians(deg)!r}\n    o.rotation_euler=e\nprint('ok')"
        )
        say = f"Rotated {deg:g} degrees."
    elif re.search(
        r"\b(scale|resize|bigger|larger|smaller|shrink|enlarge|taller|wider|double|twice|halve|half)\b",
        t,
    ):
        p = _pct(t)
        grow = re.search(r"bigger|larger|enlarge|taller|wider|increase", t)
        if re.search(r"\bdouble|twice\b", t):
            f = 2.0
        elif re.search(r"\bhalf|halve\b", t):
            f = 0.5
        elif p is not None:
            f = (
                1 + p / 100
                if grow
                else (
                    p / 100 if re.search(r"scale|resize", t) else max(0.01, 1 - p / 100)
                )
            )
        else:
            f = 1.25 if grow else 0.8
        axes = (2,) if "taller" in t else ((0, 1) if "wider" in t else (0, 1, 2))
        sets = "".join(f"    s[{a}]*={f!r}\n" for a in axes)
        code = (
            ROOT_CODE
            + f"for o in roots:\n    s=list(o.scale)\n{sets}    o.scale=s\nprint('ok')"
        )
        say = f"Scaled by {f:g}."
    elif re.search(r"\b(move|shift|nudge|raise|lower)\b", t):
        n = re.search(_NUM, t)
        d = float(n.group(1)) if n else 1.0
        if "cm" in t:
            d /= 100
        vec = next(
            (
                v
                for w, v in (
                    ("up", (0, 0, 1)),
                    ("raise", (0, 0, 1)),
                    ("down", (0, 0, -1)),
                    ("lower", (0, 0, -1)),
                    ("left", (-1, 0, 0)),
                    ("right", (1, 0, 0)),
                    ("forward", (0, 1, 0)),
                    ("back", (0, -1, 0)),
                )
                if re.search(rf"\b{w}", t)
            ),
            None,
        )
        if vec is None:
            return None
        x, y, z = (d * c for c in vec)
        code = (
            ROOT_CODE
            + f"for o in roots:\n    o.location.x+={x!r}; o.location.y+={y!r}; o.location.z+={z!r}\nprint('ok')"
        )
        say = "Moved."
    else:
        cm = re.search(
            r"\b(?:paint|color|colour|make|turn)\b.*?\b(" + "|".join(COLORS) + r")\b", t
        )
        if cm:
            r, g, b = COLORS[cm.group(1)]
            code = ROOT_CODE + (
                "n=0\nfor o in objs:\n    for ms in o.material_slots:\n        m=ms.material\n        if m and m.use_nodes:\n"
                "            bs=m.node_tree.nodes.get('Principled BSDF')\n            if bs:\n"
                f"                bs.inputs['Base Color'].default_value=({r},{g},{b},1); n+=1\nprint('ok',n)"
            )
            say = f"Painted {cm.group(1)}."
    if code is None:
        return None
    ensure_baseline(slug)
    if "ok" not in run_py(code):
        return None
    frame_view()
    snapshot(slug, instruction[:80])
    return say


# ---------------------------------------------------------------- codex edit


def scene_context(slug: str) -> str:
    code = (
        ROOT_CODE
        + """
import json
rows = []
for o in objs:
    mats = [ms.material.name for ms in o.material_slots if ms.material]
    rows.append({'name': o.name, 'type': o.type, 'parent': o.parent.name if o.parent else None,
                 'loc': [round(v, 3) for v in o.location], 'dim': [round(v, 3) for v in o.dimensions],
                 'mats': mats})
print(json.dumps(rows[:60]))
"""
    )
    try:
        return run_py(code)[:6000]
    except Exception as e:  # noqa: BLE001
        return f"(context unavailable: {e})"


def _style(slug: str | None = None) -> str:
    """Base hologram style; the motion-rig rules only for manipulatable projects (those with commands.json)."""
    try:
        txt = (Path.home() / "deck/lib/3d_style.md").read_text()
        if slug and (ROOT / slug / "commands.json").is_file():
            txt += "\n\n" + (Path.home() / "deck/lib/3d_style_rig.md").read_text()
        return txt
    except OSError:
        return ""


def codex_edit(
    instruction: str, slug: str, timeout: float = EDIT_TIMEOUT_S
) -> tuple[bool, str]:
    ctx = scene_context(slug)
    out_dir = ROOT / slug
    brief = (
        "You edit an EXISTING 3D model live in Blender 5.1 through the 'blender' MCP server "
        "(execute_blender_code, get_scene_info, get_viewport_screenshot). The model is already in the scene.\n"
        f"Project: {slug}. Current objects (JSON): {ctx}\n"
        f"EDIT REQUEST: {instruction}\n"
        "Rules:\n"
        "- Modify the existing objects in place with bpy/bmesh (change materials, scale parts about their own "
        "origin or the part's base, add/duplicate/parent new parts). Do NOT delete and rebuild the whole model "
        "and do NOT clear the scene. Keep the camera and lights. Keep new parts parented under the model's root empty.\n"
        "- For a colour change set the Principled BSDF Base Color of the right part's material (copy the "
        "material first if it is shared with parts that must not change).\n"
        "- Do NOT save, export or open files; the host does that. Reply with one short line saying what changed.\n"
        "- Any NEW part or material you add must follow the STYLE block below; do not restyle existing parts unless asked.\n\n"
        + _style(slug)
    )
    cmd = [
        CODEX,
        "exec",
        "-p",
        "blender",
        "-m",
        os.environ.get("JARVIS_3D_MODEL", "gpt-6.1-sol"),
        "-c",
        "model_reasoning_effort=" + os.environ.get("JARVIS_3D_EFFORT", "medium"),
        "--approve-for-me",
        "--skip-git-repo-check",
        "-C",
        str(out_dir),
        "-o",
        str(out_dir / "last_edit.txt"),
        brief,
    ]
    try:
        with open(out_dir / "edit.log", "ab") as log:
            r = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=timeout
            )
    except subprocess.TimeoutExpired:
        return False, "The edit timed out."
    try:
        said = (out_dir / "last_edit.txt").read_text().strip()
    except OSError:
        said = ""
    return r.returncode == 0, said or (
        "Edit done." if r.returncode == 0 else "Codex failed."
    )


def _unchanged(slug: str) -> bool:
    """True when the live scene exports byte-identical to the head version
    (Codex can exit 0 without having changed anything)."""
    data = load_versions(slug)
    if not data["head"]:
        return False
    tmp = ROOT / slug / "versions" / "_probe.glb"
    try:
        _export_glb(tmp)
        return (
            tmp.read_bytes()
            == Path(data["versions"][data["head"] - 1]["glb"]).read_bytes()
        )
    except Exception:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _regen_commands(slug: str) -> None:
    """Codex edits can add/rename movable parts: rewrite commands.json in the background."""
    import threading

    def _run() -> None:
        try:
            subprocess.run(
                [str(Path.home() / "deck/bin/jarvis-gen-commands"), str(ROOT / slug)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=600,
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def edit(instruction: str, slug: str | None = None, allow_fast: bool = True) -> dict:
    """Full edit pipeline. Returns {ok, say, version, slug, fast}."""
    slug = slug or current_slug()
    if not slug:
        return {
            "ok": False,
            "say": "There is no 3D model yet, Sir. Ask me to make one.",
        }
    if not ensure_blender():
        return {"ok": False, "say": "Blender is not running, Sir."}
    ensure_scene(slug)
    if allow_fast:
        try:
            said = fast_edit(instruction, slug)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "say": f"Quick edit failed: {e}"[:200], "slug": slug}
        if said:
            return {
                "ok": True,
                "say": said,
                "slug": slug,
                "fast": True,
                "version": get_state().get("version"),
            }
    ensure_baseline(slug)
    ok, said = codex_edit(instruction, slug)
    if not ok:
        return {"ok": False, "say": said, "slug": slug}
    if _unchanged(slug):
        return {"ok": False, "say": f"Nothing changed, Sir. {said}"[:240], "slug": slug}
    frame_view()
    n = snapshot(slug, instruction[:80])
    if (
        ROOT / slug / "commands.json"
    ).is_file():  # only manipulatable objects carry commands
        _regen_commands(slug)
    return {"ok": True, "say": said, "slug": slug, "version": n, "fast": False}


def after_make3d(paths: list[str]) -> None:
    """Hook: called when make3d finishes. Opens Blender view + HUD viewer. Never raises."""
    try:
        glb = next((p for p in paths if p.endswith(".glb")), None)
        if not glb:
            return
        _cmd(
            "PROJECT",
            slug=Path(glb).parent.name,
            open=True,
            rotate=True,
            zoom=1.0,
            wire=True,
            explode=False,
            scan=False,
            spin_axis="y",  # fresh object: upright turntable, no leftover tilt/axis
            pitch=0.0,
            yaw=0.0,
            roll=0.0,
            speed=1.0,
            dir=1,
        )
        open_in_blender(glb)
        close_blender()
    except Exception:
        pass
