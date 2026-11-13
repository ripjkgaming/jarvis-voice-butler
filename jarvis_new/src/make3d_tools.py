"""Voice tool: build a 3D object from scratch (Codex CLI driving Blender MCP).

Calls ~/deck/bin/jarvis-make3d asynchronously (it can take minutes), then
reports the exported .glb/.blend/preview paths.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from jarvis_rt import RunContext, function_tool

SCRIPT = os.path.expanduser("~/deck/bin/jarvis-make3d")
TIMEOUT_S = 900


async def run_make3d(prompt: str, script: str = SCRIPT, timeout: float = TIMEOUT_S):
    """Run the script; return (ok, paths, error)."""
    proc = await asyncio.create_subprocess_exec(
        script,
        prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return False, [], "timed out"
    paths = [ln.strip() for ln in out.decode().splitlines() if ln.startswith("/")]
    return proc.returncode == 0 and bool(paths), paths, err.decode()[-300:]


class Make3DTools:
    """One voice tool: make_3d_object."""

    @property
    def tools(self) -> list:
        return [self.make_3d_object]

    @function_tool()
    async def make_3d_object(self, context: RunContext, description: str) -> dict:
        """Model a 3D object from scratch in Blender and export it (.glb,
        .blend and a preview PNG under ~/jarvis-projects/3d/). Takes a few
        minutes, so it runs in the BACKGROUND: it returns at once and Jarvis
        announces when the hologram is ready. Use when Sir asks to make, model or build a 3D object. For 4D shapes (tesseract, hypercube, 5/16/24/600-cell, duoprism) use make_4d_shape instead.
        Default is FAST: a plain hologram with no moving parts and no voice
        commands. Only when Sir says he wants a "manipulatable" / interactive /
        controllable object (moving parts, voice commands for it) include the
        word "manipulatable" in the description; that slower mode adds a motion
        rig and writes object-specific voice commands.

        Args:
            description: What to model, e.g. "a low-poly desk lamp" or, if Sir asked for moving parts and commands, "a manipulatable low-poly desk lamp".
        """
        description = (description or "").strip()
        if not description:
            return {"say": "What should I model, Sir?"}
        task = asyncio.create_task(_build_in_background(description))
        _BG.add(task)  # keep a reference so the task is not garbage-collected
        task.add_done_callback(_BG.discard)
        return {
            "say": (
                "On it, Sir. I'm building it in the background and will project it "
                "the moment it's ready. You can keep talking to me meanwhile."
            ),
            "background": True,
        }


_BG: set[asyncio.Task] = set()


def build_running() -> bool:
    """True while a make_3d_object build is still in flight (keeps the call open)."""
    return any(not t.done() for t in _BG)


def _announce(text: str, key: str) -> None:
    try:
        import notify

        notify.send(
            text[:300],
            title="Jarvis - 3D build",
            kind="task",
            source="make3d",
            urgency="info",
            speak_text=text[:300],
            fingerprint=f"make3d:{key}",
        )
    except Exception:
        pass


_REMAKE = re.compile(
    r"\b(re-?make|re-?generate|re-?build|fresh|from scratch|new version)\b", re.I
)


def stored_paths(description: str) -> list[str]:
    """Paths of an earlier generation of this exact description, else []. Mirrors the
    slug rule in ~/deck/bin/jarvis-make3d so a repeat request reuses the stored files."""
    slug = (
        re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")[:40].rstrip("-")
        or "object"
    )
    root = Path(
        os.environ.get("MAKE3D_OUTDIR") or Path.home() / "jarvis-projects" / "3d"
    )
    glb = root / slug / f"{slug}.glb"
    return [str(glb)] if glb.is_file() and glb.stat().st_size > 0 else []


async def _build_in_background(description: str) -> None:
    """Run the (minutes-long) build off the voice turn, then project + announce."""
    key = str(abs(hash(description)))
    paths = [] if _REMAKE.search(description) else stored_paths(description)
    if paths:  # already generated before: project the stored copy, no rebuild
        try:
            import model3d

            await asyncio.to_thread(model3d.after_make3d, paths)
        except Exception:
            pass
        _announce(
            "Your 3D object is ready and projecting, Sir. Say dismiss when you are done.",
            key,
        )
        return
    try:
        ok, paths, err = await run_make3d(description)
    except Exception:
        _announce("I could not start the 3D build, Sir.", key)
        return
    if not ok:
        _announce("The 3D build failed, Sir.", key)
        return
    try:  # auto-open in live Blender + hologram window (additive hook)
        import model3d

        await asyncio.to_thread(model3d.after_make3d, paths)
    except Exception:
        pass
    _announce(
        "Your 3D object is ready and projecting, Sir. Say dismiss when you are done.",
        key,
    )
