"""Always Aware capture: one burst of every monitor except the HUD's (§1.3).

Each frame is one ``spectacle -b -n -f`` image of the whole desktop. It is
decoded in memory, checked against the kscreen geometry, cropped into
per-monitor rectangles (the HUD rectangle is never cropped, never written),
sensitive windows plus the panel / notification zones are filled black,
downscaled, and only then written as JPEG into a private 0700 runtime dir.
Missing window geometry fails closed (``redaction_unavailable``).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aware import skip

FRAMES = 6
BURST_S = 10.0
ACTIVE_EDGE = 1280
SIDE_EDGE = 960
DEDUPE_BITS = 6
FPS = 0.6
#: Always-black zones in monitor-local logical px (over-redaction is fine):
#: the panel strip (task titles, tray) and the notification popup corner.
PANEL_PX = 48
NOTIFY_W, NOTIFY_H = 460, 380


def capture_set(screens: list[dict], hud: str | None) -> list[dict]:
    """Monitors to capture. Unknown HUD = exclude the primary (the shell's
    default and hud_screen()'s own fallback)."""
    if not screens:
        return []
    if hud is None:
        prios = [s for s in screens if s.get("priority") is not None]
        primary = min(prios, key=lambda s: s["priority"]) if prios else screens[0]
        hud = primary["name"]
    return [s for s in screens if s["name"] != hud]


def _bbox(screens: list[dict]) -> tuple[int, int, int, int]:
    x0 = min(s["x"] for s in screens)
    y0 = min(s["y"] for s in screens)
    x1 = max(s["x"] + s["w"] for s in screens)
    y1 = max(s["y"] + s["h"] for s in screens)
    return x0, y0, x1 - x0, y1 - y0


def scale_for(shape: tuple, screens: list[dict]) -> tuple[float, float] | None:
    """Pixel-per-logical-px scale of a full-desktop image, or None when the
    image does not match the monitor set (hot-plug, unexpected scaling)."""
    if not screens or len(shape) < 2:
        return None
    _, _, w, h = _bbox(screens)
    sy, sx = shape[0] / h, shape[1] / w
    if abs(sx - sy) > 0.02 or sx <= 0:
        return None
    return (round(sx, 3), round(sy, 3))


def crop(img: np.ndarray, screen: dict, screens: list[dict], scale) -> np.ndarray:
    x0, y0, _, _ = _bbox(screens)
    sx, sy = scale
    left = round((screen["x"] - x0) * sx)
    top = round((screen["y"] - y0) * sy)
    right = round((screen["x"] - x0 + screen["w"]) * sx)
    bottom = round((screen["y"] - y0 + screen["h"]) * sy)
    return img[top:bottom, left:right].copy()


def _on(rect: tuple, screen: dict) -> tuple | None:
    """Intersect a global logical rect with a screen -> monitor-local rect."""
    x, y, w, h = rect
    lx0 = max(x, screen["x"])
    ly0 = max(y, screen["y"])
    lx1 = min(x + w, screen["x"] + screen["w"])
    ly1 = min(y + h, screen["y"] + screen["h"])
    if lx1 <= lx0 or ly1 <= ly0:
        return None
    return (lx0 - screen["x"], ly0 - screen["y"], lx1 - lx0, ly1 - ly0)


def _brave_denied(title: str, tabs: list[dict], rules) -> bool:
    """A Brave window whose caption starts with a denied tab's title."""
    head = (title or "").lower()
    for t in tabs or []:
        tt = str(t.get("title") or "").strip().lower()
        if tt and head.startswith(tt[:60]) and skip.url_denied(t.get("url", ""), rules):
            return True
    return False


def zones(screen: dict) -> list[tuple]:
    w, h = screen["w"], screen["h"]
    return [
        (0, h - PANEL_PX, w, PANEL_PX),
        (max(0, w - NOTIFY_W), max(0, h - PANEL_PX - NOTIFY_H), NOTIFY_W, NOTIFY_H),
    ]


def sensitive_window(win: dict, tabs: list[dict], rules) -> bool:
    if win.get("sensitive"):
        return True
    app, title = win.get("app", ""), win.get("title", "")
    if skip.is_sensitive(app, title, "", rules):
        return True
    return "brave" in app.lower() and _brave_denied(title, tabs, rules)


def redaction_rects(windows: list[dict], screen: dict, rules, tabs) -> list[tuple]:
    """Monitor-local logical rects to black out on this screen. Pure."""
    rects: list[tuple] = []
    for win in windows:
        if win.get("minimized"):
            continue
        if not sensitive_window(win, tabs, rules):
            continue
        local = _on((win["x"], win["y"], win["w"], win["h"]), screen)
        if local:
            rects.append(local)
    rects.extend(zones(screen))
    return rects


def blackout(img: np.ndarray, rects: list[tuple], scale=(1.0, 1.0)) -> np.ndarray:
    sx, sy = scale
    h, w = img.shape[:2]
    for x, y, rw, rh in rects:
        x0 = max(0, int(x * sx) - 2)
        y0 = max(0, int(y * sy) - 2)
        x1 = min(w, int((x + rw) * sx + 0.999) + 2)
        y1 = min(h, int((y + rh) * sy + 0.999) + 2)
        img[y0:y1, x0:x1] = 0
    return img


def downscale(img: np.ndarray, long_edge: int) -> np.ndarray:
    import cv2

    h, w = img.shape[:2]
    scale = long_edge / max(h, w)
    if scale >= 1.0:
        return img
    return cv2.resize(
        img,
        (round(w * scale), round(h * scale)),
        interpolation=cv2.INTER_AREA,
    )


def dhash(img: np.ndarray, size: int = 16) -> int:
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    small = cv2.resize(gray, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    out = 0
    for bit in bits:
        out = (out << 1) | int(bit)
    return out


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def monitor_changed(name: str, h: int, windows: list, prev: dict) -> bool:
    last = prev.get(name)
    if not last:
        return True
    if sorted(last.get("windows") or []) != sorted(windows):
        return True
    return hamming(int(last.get("hash", 0)), h) > DEDUPE_BITS


def private_workdir() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    root = base / "jarvis-aware"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    d = Path(tempfile.mkdtemp(prefix="tick-", dir=root))
    os.chmod(d, 0o700)
    return d


def _low_prio() -> list[str]:
    argv = ["nice", "-n", "19"]
    if shutil.which("ionice"):
        argv += ["ionice", "-c3"]
    return argv


def ffmpeg_argv(pattern: Path, out: Path, fps: float = FPS) -> list[str]:
    return [
        *_low_prio(),
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(pattern),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "32",
        "-pix_fmt",
        "yuv420p",
        "-vf",
        "pad=ceil(iw/2)*2:ceil(ih/2)*2",
        "-threads",
        "1",
        str(out),
    ]


def encode(workdir: Path, key: str, out: Path, run=subprocess.run) -> Path | None:
    try:
        proc = run(
            ffmpeg_argv(workdir / f"{key}_%02d.jpg", out),
            capture_output=True,
            timeout=60,
        )
        if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
            return out
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def shoot_desktop(run=subprocess.run) -> np.ndarray | None:
    """One full-desktop frame, decoded in memory; the PNG is unlinked at once."""
    import cv2

    fd, path = tempfile.mkstemp(suffix=".png", dir=private_workdir())
    os.close(fd)
    try:
        proc = run(
            [*_low_prio(), "spectacle", "-b", "-n", "-f", "-o", path],
            capture_output=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return None
        return cv2.imread(path, cv2.IMREAD_COLOR)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)
        with contextlib.suppress(OSError):
            os.rmdir(os.path.dirname(path))


@dataclass
class Burst:
    ok: bool
    reason: str = ""
    workdir: Path | None = None
    monitors: dict = field(default_factory=dict)
    seconds: float = 0.0

    def cleanup(self) -> None:
        if self.workdir is not None:
            shutil.rmtree(self.workdir, ignore_errors=True)


def burst(
    *,
    shoot,
    screens: list[dict],
    hud: str | None,
    windows_fn,
    tabs: list[dict],
    active_monitor: str | None,
    workdir: Path,
    sleep=time.sleep,
    rules=None,
    frames: int = FRAMES,
    span_s: float = BURST_S,
) -> Burst:
    """Take the burst; per monitor: frames (paths), hash1/hash6, window ids.

    Redaction is re-read before the burst; geometry is fixed for its 10 s.
    """
    import cv2

    rules = rules or skip.Rules()
    targets = capture_set(screens, hud)
    if not targets:
        return Burst(False, "only_hud_monitor")
    wins = windows_fn()
    if wins is None:
        return Burst(False, "redaction_unavailable")
    plans = {}
    for s in targets:
        on = [
            w
            for w in wins
            if not w.get("minimized") and _on((w["x"], w["y"], w["w"], w["h"]), s)
        ]
        plans[s["name"]] = {
            "screen": s,
            "rects": redaction_rects(wins, s, rules, tabs),
            "windows": [str(w.get("id") or w.get("title", ""))[:80] for w in on],
            "frames": [],
        }
    t0 = time.monotonic()
    gap = span_s / max(1, frames - 1)
    for i in range(frames):
        img = shoot()
        if img is None:
            _wipe(workdir)
            return Burst(False, "capture")
        scale = scale_for(img.shape, screens)
        if scale is None:
            del img
            _wipe(workdir)
            return Burst(False, "monitors_changed")
        for k, (name, plan) in enumerate(plans.items()):
            part = crop(img, plan["screen"], screens, scale)
            blackout(part, plan["rects"], scale)
            edge = ACTIVE_EDGE if name == active_monitor else SIDE_EDGE
            part = downscale(part, edge)
            if i == 0:
                plan["hash1"] = dhash(part)
            if i == frames - 1:
                plan["hash6"] = dhash(part)
            path = workdir / f"m{k}_{i:02d}.jpg"
            cv2.imwrite(str(path), part, [cv2.IMWRITE_JPEG_QUALITY, 80])
            os.chmod(path, 0o600)
            plan["frames"].append(path)
            plan["key"] = f"m{k}"
            plan["size"] = part.shape[1::-1]
        del img
        if i < frames - 1:
            sleep(gap)
    out = {}
    for name, plan in plans.items():
        plan.pop("screen", None)
        out[name] = plan
    return Burst(True, "", workdir, out, time.monotonic() - t0)


def _wipe(workdir: Path) -> None:
    for p in workdir.glob("*.jpg"):
        with contextlib.suppress(OSError):
            p.unlink()


# --------------------------------------------------------------- live windows


def live_windows(probe_sensitive: bool = True) -> list[dict] | None:
    """Taskbar windows with fresh KWin geometry; None = cannot redact.

    The KWin push (active_window.windows) gives ids; geometry comes from
    org.kde.KWin.getWindowInfo per uuid. Sensitive dialogs outside the
    taskbar (polkit, pinentry...) are found by a KRunner probe. A
    sensitive window whose geometry cannot be read fails the tick closed.
    """
    try:
        import active_window
        from system import kwin_windows
        from system import monitor_window_move as mwm
    except Exception:
        return None
    pushed = active_window.windows()
    if not pushed:
        return None
    seen: dict[str, dict] = {}
    for w in pushed:
        seen[str(w.get("id"))] = {
            "app": w.get("app", ""),
            "title": w.get("title", ""),
            "minimized": w.get("minimized", False),
        }
    if probe_sensitive:
        for word in (
            "keepass",
            "bitwarden",
            "1password",
            "kwallet",
            "polkit",
            "pinentry",
            "password",
            "authentication",
        ):
            try:
                for uuid, title in kwin_windows.find(word):
                    seen.setdefault(
                        uuid,
                        {"app": "", "title": title, "minimized": False, "probe": word},
                    )
            except Exception:
                continue
    rules = skip.Rules()
    out = []
    for uuid, meta in seen.items():
        info = mwm._info(uuid)
        if info is None:
            if (
                meta.get("probe")
                or skip.is_sensitive(meta["app"], meta["title"], "", rules)
            ) and not meta.get("minimized"):
                return None
            continue
        out.append(
            {
                "id": uuid,
                "app": str(info.get("resourceClass") or meta["app"]),
                "title": str(info.get("caption") or meta["title"]),
                "x": int(info["x"]),
                "y": int(info["y"]),
                "w": int(info["width"]),
                "h": int(info["height"]),
                "minimized": bool(info.get("minimized", meta.get("minimized"))),
                # KRunner probe hits are sensitive by construction.
                "sensitive": bool(meta.get("probe")),
            }
        )
    return out
