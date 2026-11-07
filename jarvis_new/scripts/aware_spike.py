"""Always Aware Phase 0 spike (docs/always-aware/PLAN.md §8, SPIKE.md).

Subcommands (all print JSON):

  encode         synthetic fixture frames -> per-monitor MP4, size + CPU time
  gemini N       N real Gemini calls (max 5) on synthetic clips only:
                 1 = 1 monitor request-level MEDIUM, 2 = 2 monitors LOW,
                 3 = 2 monitors per-part resolution, 4 = form, 5 = password
  cdp            attach to the live Brave over CDP, list page count, open one
                 scratch background window (about:blank), close it, detach
  worker         headless Playwright Chromium + an unpacked copy of the
                 locke-ext worker: windows.create({tabId}) / tabs.move keep
                 sessionStorage + typed input (no reload)
  capture        LOCAL ONLY, run by Sir: time `spectacle -b -n -f` x10, check
                 image size vs kscreen geometry. Nothing is uploaded; frames
                 are deleted immediately.

Usage: uv run python scripts/aware_spike.py <cmd>
"""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "aware"))


def _key() -> str:
    key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if key:
        return key
    for name in (".env.local", ".env"):
        try:
            for line in (ROOT / name).read_text().splitlines():
                if line.startswith("GOOGLE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
        except OSError:
            continue
    import focus

    return focus.google_key()


def _clip(scene: str, workdir: Path, key: str, edge: int) -> bytes:
    import frames as fx

    from aware import capture

    fx.write_burst(scene, workdir, key=key, edge=edge)
    out = capture.encode(workdir, key, workdir / f"{key}.mp4")
    if out is None:
        raise SystemExit("ffmpeg encode failed")
    return out.read_bytes()


def cmd_encode() -> dict:
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for scene, edge in (("terminal", 1280), ("form", 960), ("document", 960)):
            r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            t0 = time.monotonic()
            data = _clip(scene, Path(tmp) / scene, "m0", edge)
            r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            out[scene] = {
                "edge": edge,
                "bytes": len(data),
                "wall_s": round(time.monotonic() - t0, 2),
                "ffmpeg_cpu_s": round(
                    (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime), 2
                ),
            }
    return out


def cmd_gemini(n: int) -> dict:
    import focus
    from aware import vision

    key = _key()
    if not key:
        raise SystemExit("no GOOGLE_API_KEY")
    shapes = {
        1: ([("terminal", True)], False),
        2: ([("terminal", True), ("document", False)], False),
        3: ([("terminal", True), ("document", False)], True),
        4: ([("form", True)], False),
        5: ([("password", True)], False),
    }
    clips_spec, per_part = shapes[n]
    with tempfile.TemporaryDirectory() as tmp:
        clips = []
        for k, (scene, active) in enumerate(clips_spec):
            data = _clip(scene, Path(tmp) / f"s{k}", f"m{k}", 1280 if active else 960)
            label = f"M{k + 1} {'main, active window here' if active else 'side'}"
            clips.append({"label": label, "mp4": data, "active": active})
        ctx = {
            "time": time.strftime("%H:%M"),
            "monitors": [
                {
                    "label": c["label"],
                    "windows": [
                        "org.kde.konsole: pytest — Konsole"
                        if i == 0
                        else "okular: chapter3.pdf"
                    ],
                }
                for i, c in enumerate(clips)
            ],
            "active": "org.kde.konsole: pytest — Konsole",
        }
        raw = {}

        def post(url, body, k):
            t0 = time.monotonic()
            try:
                payload = focus._gemini_post(url, body, k, timeout=90)
            except Exception as exc:
                raw["http_error"] = f"{type(exc).__name__} {getattr(exc, 'code', '')}"
                if hasattr(exc, "read"):
                    raw["http_body"] = exc.read().decode("utf8", "replace")[:800]
                raise
            raw["latency_s"] = round(time.monotonic() - t0, 2)
            raw["usage"] = payload.get("usageMetadata")
            return payload

        try:
            res = vision.ask(
                clips,
                ctx,
                key=key,
                models=focus.gemini_models()[:1],
                post=post,
                per_part=per_part,
            )
        except (
            Exception
        ) as exc:  # report the API's complaint (e.g. per-part unsupported)
            detail = ""
            if hasattr(exc, "__cause__") and hasattr(exc.__cause__, "read"):
                detail = exc.__cause__.read().decode()[:600]
            return {
                "shape": n,
                "per_part": per_part,
                "error": str(exc)[:300],
                "detail": detail,
                **raw,
            }
        return {
            "shape": n,
            "per_part": per_part,
            "bytes_up": res["bytes_up"],
            "model": res["model"],
            "tokens": res["tokens"],
            "obs": res["obs"],
            **raw,
        }


def cmd_cdp() -> dict:
    from aware import cdp

    port = cdp.port()
    ver = cdp.version(port)
    if not ver:
        return {"ok": False, "error": f"no CDP on 127.0.0.1:{port}"}
    pages_before = [t for t in cdp.targets(port) if t.get("type") == "page"]
    browser_ws = ver["webSocketDebuggerUrl"]
    created = cdp.call(
        browser_ws,
        "Target.createTarget",
        {"url": "about:blank", "newWindow": True, "background": True},
    )
    tid = created.get("targetId")
    time.sleep(1.0)
    closed = cdp.call(browser_ws, "Target.closeTarget", {"targetId": tid})
    time.sleep(0.5)
    pages_after = [t for t in cdp.targets(port) if t.get("type") == "page"]
    return {
        "ok": True,
        "browser": ver.get("Browser"),
        "pages_before": len(pages_before),
        "pages_after": len(pages_after),
        "scratch_target": bool(tid),
        "closed": bool(closed.get("success", True)),
        "alive_after": bool(cdp.version(port)),
    }


def cmd_worker(*, runner=None) -> dict:
    """Exercise the extension in an isolated browser, never the live profile."""
    runner = runner or subprocess.run
    result = runner(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_aware_brave.py::test_live_takeover_keeps_state_moves_back_and_never_reloads",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return {
        "ok": result.returncode == 0,
        "scope": "throwaway Chromium profile and local fake form",
        "kwin": "mocked",
        "returncode": result.returncode,
        "output": (result.stdout + result.stderr)[-6000:],
    }


def cmd_capture() -> dict:
    from aware import capture
    from system import monitor_window_move as mwm

    screens = mwm.monitors()
    hud = mwm.hud_screen(screens)
    times, shapes = [], []
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    for _ in range(10):
        t0 = time.monotonic()
        img = capture.shoot_desktop()
        times.append(round(time.monotonic() - t0, 2))
        shapes.append(None if img is None else list(img.shape[:2]))
        del img
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "screens": screens,
        "hud": hud,
        "capture_set": [s["name"] for s in capture.capture_set(screens, hud)],
        "shot_s": times,
        "shapes": shapes,
        "scale": capture.scale_for(tuple(shapes[0]), screens)
        if shapes and shapes[0]
        else None,
        "cpu_s_total": round(
            (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime), 2
        ),
        "windows_with_geometry": len(capture.live_windows() or []),
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "encode":
        res = cmd_encode()
    elif cmd == "gemini":
        n = int(argv[1]) if len(argv) > 1 else 1
        if not 1 <= n <= 5:
            raise SystemExit("shape 1..5")
        res = cmd_gemini(n)
    elif cmd == "cdp":
        res = cmd_cdp()
    elif cmd == "worker":
        res = cmd_worker()
    elif cmd == "capture":
        res = cmd_capture()
    else:
        print(__doc__)
        return 2
    print(json.dumps(res, indent=2, default=str))
    return 1 if cmd == "worker" and not res["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
