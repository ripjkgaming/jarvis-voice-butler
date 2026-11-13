"""Voice tools: edit the live 3D model (edit_3d_object) and drive the HUD viewer (view_3d).

Edits run against the SAME live Blender session (src/model3d.py): trivial
commands go straight to the Blender socket, the rest to Codex gpt-6.1-sol (medium).
"""

from __future__ import annotations

import asyncio

from jarvis_rt import RunContext, function_tool


_BG: set = set()


def _physics_tools() -> list:
    from physics_tools import PhysicsTools

    return PhysicsTools().tools


async def _finish_edit(job, instruction: str) -> None:
    """Announce a slow edit when it finishes; reopen the hologram if dismissed."""
    import model3d
    from make3d_tools import _announce

    key = str(abs(hash(instruction)))
    try:
        res = await job
    except Exception:
        _announce("The 3D edit failed, Sir.", key)
        return
    if res.get("ok") and not model3d.get_state().get("open"):
        await asyncio.to_thread(model3d.view_command, "open")
    _announce(res.get("say") or "Edit finished, Sir.", key)


class Model3DTools:
    @property
    def tools(self) -> list:
        return [
            self.edit_3d_object,
            self.view_3d,
            self.control_3d_part,
            self.make_4d_shape,
            self.make_molecule,
            self.make_colored_model,
            *_physics_tools(),
        ]

    @function_tool()
    async def edit_3d_object(self, context: RunContext, instruction: str) -> dict:
        """Edit the current 3D model live in Blender and refresh the viewer:
        "make it taller", "paint it red", "add a second arm", "rotate it",
        "make the shade red and 30% bigger", "undo", "redo", "save", "export".
        Simple ones are instant; part-level edits take a minute or two. Every
        edit is a numbered version, so "undo" always works.

        Args:
            instruction: The edit, in Sir's words.
        """
        instruction = (instruction or "").strip()
        if not instruction:
            return {"say": "What should I change, Sir?"}
        import model3d

        job = asyncio.ensure_future(asyncio.to_thread(model3d.edit, instruction))
        try:  # quick edits answer inline; slow (Codex) edits continue in the background
            res = await asyncio.wait_for(asyncio.shield(job), 6)
        except asyncio.TimeoutError:
            _BG.add(job)
            job.add_done_callback(_BG.discard)
            asyncio.ensure_future(_finish_edit(job, instruction))
            return {
                "say": "Working on that edit in the background, Sir. I'll tell you when it's done.",
                "background": True,
            }
        except Exception:
            return {"say": "The 3D edit failed, Sir."}
        if res.get("ok"):
            # Already projecting: the HUD morphs the live hologram when the glb
            # changes. Only (re)open when it was dismissed.
            if not model3d.get_state().get("open"):
                await asyncio.to_thread(model3d.view_command, "open")
        return {
            k: v
            for k, v in res.items()
            if k in ("say", "version", "slug", "fast", "ok")
        }

    @function_tool()
    async def view_3d(
        self, context: RunContext, action: str, degrees: float | None = None
    ) -> dict:
        """Control the 3D hologram projection by voice: show/open, close/dismiss,
        rotate, stop, faster, slower, reverse, zoom in/out, reset, wireframe,
        solid, explode (exploded view: every piece spreads outward from the
        object's centre), assemble, scan, rebuild. "rebuild"/"replay" replays the
        build-up animation of the CURRENT hologram; never use make_3d_object for it.
        For 4D shapes: rotate_4d ("move through the fourth dimension"), stop_4d,
        plane_xw / plane_yw / plane_zw / plane_all / double_rotation (which 4D plane).
        Orientation on all 3 axes: tilt_up / tilt_down (up-down, pitch), turn_left / turn_right
        (sideways, yaw), roll_left / roll_right (the third axis, like a wheel, roll), level
        (back to upright); each moves 30 degrees unless Sir gives an amount (pass degrees).
        spin_vertically / spin_sideways / spin_like_a_wheel pick which axis the auto-rotation spins about.

        Args:
            action: One of open, close, rotate, stop, faster, slower, reverse, zoom_in, zoom_out, reset, wireframe, solid, explode, assemble, scan, rebuild, rotate_4d, stop_4d, plane_xw, plane_yw, plane_zw, plane_all, double_rotation, tilt_up, tilt_down, turn_left, turn_right, roll_left, roll_right, level, spin_vertically, spin_sideways, spin_like_a_wheel.
            degrees: Optional angle for tilt/turn/roll (default 30).
        """
        import model3d

        say = await asyncio.to_thread(model3d.view_command, action or "open", degrees)
        return {"say": say}

    @function_tool()
    async def control_3d_part(self, context: RunContext, command: str) -> dict:
        """Move or light up one specific PART of the projected 3D object, by voice.
        Works for any model: lights (on/off/dim/brighten/colour), doors/lids/hoods
        (open/close), wheels/fans/propellers (spin/stop/faster/slower/reverse),
        arms/necks/heads/shades (raise/lower/turn left/right/wave), drawers (extend/retract).
        "turn the lamp off", "open the left door", "spin the fan", "make the headlights blue",
        "raise the arm", "what can I control".

        Args:
            command: Sir's words about the part and what to do with it.
        """
        import model3d

        say = await asyncio.to_thread(model3d.part_command, command or "")
        return {"say": say}

    @function_tool()
    async def make_4d_shape(self, context: RunContext, name: str) -> dict:
        """Instantly project a 4D shape that animates through the fourth dimension
        (instant, no Blender): tesseract / hypercube, 5-cell, 16-cell, 24-cell,
        600-cell, duoprisms ("5-8 duoprism"), hypersphere. Use for any request for
        a 4D / four-dimensional / hyper shape, INSTEAD of make_3d_object.

        Args:
            name: The 4D shape, e.g. "tesseract" or "24-cell".
        """
        import re
        import shape4d
        import model3d

        got = shape4d.build(name or "")
        if not got:
            return {"say": f"I can project these 4D shapes, Sir: {shape4d.SUPPORTED}."}
        label, v, e = got
        slug = "4d-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
        import recipes

        await asyncio.to_thread(recipes.save, slug, {"kind": "shape4d", "name": label})
        await asyncio.to_thread(
            model3d.set_state,
            cmd="PROJECT",
            cmd_seq=int(model3d.get_state().get("cmd_seq") or 0) + 1,
            slug=slug,
            open=True,
            rotate=True,
            rot4d=True,
            plane4d="all",
            zoom=1.0,
            wire=True,
            explode=False,
            scan=False,
        )
        return {
            "say": f"Projecting the {label}, Sir. Say 'rotate through the fourth dimension' to change the plane, or 'explode' to take it apart.",
            "slug": slug,
        }

    @function_tool()
    async def make_molecule(self, context: RunContext, name_or_smiles: str) -> dict:
        """Instantly build a coloured holographic molecule (atoms coloured by element, real
        bonds incl. double/triple, correct bond angles): water, CO2, ammonia, methane,
        ethanol, benzene, glucose, caffeine, aspirin, DNA bases, acids... or ANY compound
        via its SMILES string (you know SMILES; pass one when the name is not common).
        Use for any molecule / compound / chemical bond / "build H2O" request, INSTEAD of
        make_3d_object. Instant, no Blender.

        Args:
            name_or_smiles: Common name, formula (H2O, CO2) or SMILES (e.g. "CC(=O)O").
        """
        import colorbuild
        import model3d

        try:
            r = await asyncio.to_thread(colorbuild.make_molecule, name_or_smiles or "")
        except Exception as e:  # noqa: BLE001
            return {"say": f"I could not build that molecule, Sir: {e}"}
        await asyncio.to_thread(
            model3d.set_state,
            cmd="PROJECT",
            cmd_seq=int(model3d.get_state().get("cmd_seq") or 0) + 1,
            slug=r["slug"],
            open=True,
            rotate=True,
            rot4d=False,
            zoom=1.0,
            wire=True,
            explode=False,
            scan=False,
        )
        return {
            "say": f"Projecting {r['label']}, {r['formula']}, Sir: {r['atoms']} atoms, {r['bonds']} bonds. Atoms are colour coded by element.",
            **r,
        }

    @function_tool()
    async def make_colored_model(
        self, context: RunContext, name: str, parts: list[dict]
    ) -> dict:
        """Instantly build ANY coloured holographic model from primitives (no Blender, about a
        second). You design it: list the parts. Use for quick coloured builds of anything
        ("build me a red house", "a coloured snowman", "a toy rocket"), when Sir wants colour or
        speed. Prefer make_3d_object only for detailed/organic models or when he asks for
        Blender quality.

        Args:
            name: Short name of the object.
            parts: Up to 400 parts, each {"shape": box|sphere|cylinder|cone|torus|pyramid|capsule|disc|tetra|octa|icosa|dodeca,
                "pos": [x,y,z] (Z is up, the object is about 2-4 units wide), "size": [sx,sy,sz] or one number,
                "rot": [rx,ry,rz] degrees, "color": name (red, blue, gold...) or "#rrggbb"}.
        """
        import colorbuild
        import model3d

        try:
            r = await asyncio.to_thread(
                colorbuild.make_build, name or "model", parts or []
            )
        except Exception as e:  # noqa: BLE001
            return {"say": f"I could not build that, Sir: {e}"}
        await asyncio.to_thread(
            model3d.set_state,
            cmd="PROJECT",
            cmd_seq=int(model3d.get_state().get("cmd_seq") or 0) + 1,
            slug=r["slug"],
            open=True,
            rotate=True,
            rot4d=False,
            zoom=1.0,
            wire=True,
            explode=False,
            scan=False,
        )
        return {
            "say": f"Projecting the coloured {r['label']}, Sir, {r['parts']} parts.",
            **r,
        }
