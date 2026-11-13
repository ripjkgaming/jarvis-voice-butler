"""Voice tools for the built-in physics engine (src/physics.py): simulations run in the BACKGROUND.

run_simulation starts a job and returns at once; Jarvis announces the numbers when it finishes.
show_simulation projects the finished run as a coloured trajectory hologram (recipe-only).
"""

from __future__ import annotations

import asyncio
import colorsys
import json
import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from jarvis_rt import RunContext, function_tool

SIMS = Path.home() / ".jarvis" / "sims"
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="physics")
_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_ORDER: list[str] = []


def _announce(text: str, key: str) -> None:
    try:
        from make3d_tools import _announce as a

        a(text, key)
    except Exception:
        pass


def _say(summary: dict) -> str:
    p = summary.get("preset", "simulation")
    if p == "ode":
        p = "equation"
    bits = []
    if "range_m" in summary:
        bits.append(
            f"range {summary['range_m']} m, peak height {summary['max_height_m']} m, flight {summary['flight_time_s']} s"
        )
    if "period_s" in summary:
        bits.append(f"period {summary['period_s']} s")
    if "min_distance_au" in summary:
        bits.append(
            f"orbit between {summary['min_distance_au']} and {summary['max_distance_au']} AU"
        )
    if "earth_distance_au_range" in summary:
        lo, hi = summary["earth_distance_au_range"]
        bits.append(f"Earth stayed {lo} to {hi} AU from the Sun")
    if "max_extent" in summary:
        bits.append(f"stayed within {summary['max_extent']} units")
    if "net_displacement" in summary:
        bits.append(
            f"the pollen grain wandered a path of {summary['path_length']} but ended only {summary['net_displacement']} from where it started, "
            f"with a diffusion coefficient near {summary['diffusion_coeff']}"
        )
    if "equations" in summary:
        bits.append(f"{summary['equations']}; final spread {summary['spread']}")
        if "msd" in summary:
            bits.append(
                f"mean square displacement {summary['msd']}"
                + (
                    f", theory {summary['expected_msd']}"
                    if "expected_msd" in summary
                    else ""
                )
            )
    if summary.get("collisions") and "net_displacement" not in summary:
        bits.append(f"{summary['collisions']} collisions")
    if "energy_drift" in summary:
        bits.append(f"energy drift {summary['energy_drift']:.1e}")
    if "max_speed" in summary and "equations" not in summary:
        bits.append(f"top speed {summary.get('max_speed')}")
    return (
        f"The {p.replace('_', ' ')} simulation is done, Sir: " + ", ".join(bits) + "."
    )


def _work(job_id: str, spec: dict) -> None:
    import physics

    job = _JOBS[job_id]
    try:
        out = physics.simulate(spec, stop=lambda: job.get("cancel", False))
        SIMS.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            SIMS / f"{job_id}.npz",
            frames=out["frames"].astype(np.float32),
            t=out["t"],
            radii=out["radii"],
            fixed=out["fixed"],
            labels=np.array(out["labels"]),
        )
        (SIMS / f"{job_id}.json").write_text(
            json.dumps({"spec": spec, "summary": out["summary"], "scene": out["scene"]})
        )
        job.update(state="done", summary=out["summary"], finished=time.time())
        with _LOCK:  # a tool call still waiting for this job returns the numbers itself, in its own reply
            handed = job.get("wait", False)
        present(job_id, announce=None if handed else _say(out["summary"]))
    except Exception as e:  # noqa: BLE001
        job.update(state="failed", error=str(e), finished=time.time())
        _announce(f"The simulation failed, Sir: {e}", job_id)


FPS = 30


def _fd(m: float, unit: str = "m") -> str:
    if unit == "AU":
        return f"{m:.3f} AU"
    return f"{m / 1000:.2f} km" if abs(m) >= 1000 else f"{m:.1f} m"


def annotate(
    preset: str,
    G: np.ndarray,
    tt: np.ndarray,
    rad: np.ndarray,
    labels: list,
    gz,
    summary: dict,
    spec: dict,
    ctr=None,
    k=1.0,
) -> list:
    """Teaching labels for the animation, in sim axes / raw units (the caller normalises). Each mark:
    {"t": "pt"|"line"|"arc"|"tag", "f": frame it appears at, "text": str, ...}."""
    marks: list[dict] = []
    nf = len(G)
    dt = float(tt[1] - tt[0]) if nf > 1 else 1.0
    if preset == "projectile":
        P = G[:, 0]
        z = P[:, 2]
        g0 = 0.0 if gz is None else float(gz)
        r0 = float(rad[0])
        apex = int(np.argmax(z))
        below = np.nonzero((z <= g0 + r0 + 0.02) & (np.arange(nf) > apex))[0]
        land = int(below[0]) if len(below) else nf - 1
        st, ap, ld = P[0], P[apex], P[land]
        h0 = float(st[2] - g0 - r0)
        marks.append(
            {
                "t": "pt",
                "f": 0,
                "p": st.tolist(),
                "text": "START" + (f"\nh = {h0:.1f} m" if h0 > 0.05 else ""),
                "c": "#7dffb0",
                "a": "r",
            }
        )
        v0 = (G[1, 0] - G[0, 0]) / dt if nf > 1 else np.array([1.0, 0, 0])
        sp0 = float(np.linalg.norm(v0))
        vh = np.array([v0[0], v0[1], 0.0])
        vh = vh / (np.linalg.norm(vh) or 1.0)
        vu = v0 / (sp0 or 1.0)
        ang = math.degrees(math.atan2(v0[2], math.hypot(v0[0], v0[1])))
        marks.append(
            {
                "t": "line",
                "f": 0,
                "a": st.tolist(),
                "b": (st + vh * 1e-9).tolist(),
                "dir": vh.tolist(),
                "len": 1.0,
                "dash": True,
                "c": "#5fe3ff",
                "text": "",
                "scene_len": True,
            }
        )
        marks.append(
            {
                "t": "line",
                "f": 0,
                "a": st.tolist(),
                "dir": vu.tolist(),
                "len": 0.9,
                "dash": False,
                "c": "#ffd24a",
                "text": f"v0 = {float(spec.get('speed', sp0)):.0f} m/s",
                "scene_len": True,
                "arrow": True,
            }
        )
        marks.append(
            {
                "t": "arc",
                "f": 0,
                "c0": st.tolist(),
                "u": vh.tolist(),
                "v": vu.tolist(),
                "r": 0.55,
                "text": f"{ang:.1f}°",
                "col": "#ffd24a",
            }
        )
        ha = float(ap[2] - g0 - r0)
        marks.append(
            {
                "t": "line",
                "f": apex,
                "a": ap.tolist(),
                "b": [float(ap[0]), float(ap[1]), g0 + r0],
                "dash": True,
                "c": "#5fe3ff",
                "text": f"HEIGHT {_fd(ha)}",
            }
        )
        marks.append(
            {
                "t": "pt",
                "f": apex,
                "p": ap.tolist(),
                "text": f"APEX\nt = {float(tt[apex]):.1f} s\nx = {_fd(float(np.linalg.norm(ap[:2] - st[:2])))}",
                "c": "#ff9f43",
                "a": "c",
            }
        )
        vimp = float(np.linalg.norm(P[land] - P[max(0, land - 1)]) / dt)
        marks.append(
            {
                "t": "pt",
                "f": land,
                "p": ld.tolist(),
                "text": f"IMPACT\nt = {float(tt[land]):.1f} s\nv = {vimp:.0f} m/s",
                "c": "#ff6b6b",
                "a": "l",
            }
        )
        rng = float(np.linalg.norm(ld[:2] - st[:2]))
        marks.append(
            {
                "t": "line",
                "f": land,
                "a": [float(st[0]), float(st[1]), g0 + r0],
                "b": [float(ld[0]), float(ld[1]), g0 + r0],
                "dash": True,
                "c": "#7dffb0",
                "text": f"RANGE {_fd(rng)}",
                "low": True,
            }
        )
    elif preset == "orbit":
        sun, pl = G[:, 0], G[:, 1]
        d = np.linalg.norm(pl - sun, axis=1)
        marks.append({"t": "tag", "f": 0, "body": 0, "text": "SUN"})
        marks.append({"t": "tag", "f": 0, "body": 1, "text": "PLANET"})
        marks.append(
            {
                "t": "line",
                "f": 0,
                "a": sun[0].tolist(),
                "b": pl[0].tolist(),
                "dash": True,
                "c": "#5fe3ff",
                "text": f"r = {_fd(float(d[0]), 'AU')}",
            }
        )
        if d.max() - d.min() > 1e-3 * d.max():
            i1, i2 = int(np.argmin(d)), int(np.argmax(d))
            marks.append(
                {
                    "t": "pt",
                    "f": i1,
                    "p": pl[i1].tolist(),
                    "text": f"PERIAPSIS\n{_fd(float(d[i1]), 'AU')}",
                    "c": "#ff9f43",
                    "a": "l",
                }
            )
            marks.append(
                {
                    "t": "pt",
                    "f": i2,
                    "p": pl[i2].tolist(),
                    "text": f"APOAPSIS\n{_fd(float(d[i2]), 'AU')}",
                    "c": "#7dffb0",
                    "a": "l",
                }
            )
    elif preset in ("pendulum", "double_pendulum"):
        marks.append({"t": "tag", "f": 0, "body": 0, "text": "PIVOT"})
        for b in range(1, G.shape[1]):
            marks.append(
                {
                    "t": "tag",
                    "f": 0,
                    "body": b,
                    "text": "BOB" if G.shape[1] == 2 else f"BOB {b}",
                }
            )
        piv = G[0, 0]
        for b in range(1, min(G.shape[1], 3)):
            rod = G[0, b] - G[0, b - 1]
            L = float(np.linalg.norm(rod))
            marks.append(
                {
                    "t": "line",
                    "f": 0,
                    "a": G[0, b - 1].tolist(),
                    "b": G[0, b].tolist(),
                    "dash": True,
                    "c": "#5fe3ff",
                    "text": f"L = {L:.2f} m",
                }
            )
            down = np.array([0.0, 0.0, -1.0])
            ang = math.degrees(math.acos(float(np.clip(np.dot(rod / L, down), -1, 1))))
            marks.append(
                {
                    "t": "arc",
                    "f": 0,
                    "c0": G[0, b - 1].tolist(),
                    "u": down.tolist(),
                    "v": (rod / L).tolist(),
                    "r": 0.45,
                    "text": f"{ang:.0f}°",
                    "col": "#ffd24a",
                }
            )
        if "period_s" in summary:
            marks.append(
                {
                    "t": "pt",
                    "f": 0,
                    "p": piv.tolist(),
                    "text": f"PERIOD {summary['period_s']} s",
                    "c": "#7dffb0",
                    "a": "c",
                    "up": 0.55,
                }
            )
    elif preset == "brownian":
        gp = G[:, 0]
        marks.append({"t": "tag", "f": 0, "body": 0, "text": "POLLEN GRAIN"})
        marks.append(
            {
                "t": "pt",
                "f": 0,
                "p": gp[0].tolist(),
                "text": "START",
                "c": "#7dffb0",
                "a": "r",
            }
        )
        d = float(summary.get("net_displacement", 0))
        marks.append(
            {
                "t": "line",
                "f": nf - 1,
                "a": gp[0].tolist(),
                "b": gp[-1].tolist(),
                "dash": True,
                "c": "#ffd24a",
                "text": f"NET DISPLACEMENT {d:.2f}",
            }
        )
        marks.append(
            {
                "t": "pt",
                "f": nf - 1,
                "p": gp[-1].tolist(),
                "text": f"END\npath {summary.get('path_length', 0)} long\nD = {summary.get('diffusion_coeff', 0)}",
                "c": "#ff9f43",
                "a": "l",
            }
        )
    elif preset == "ode":
        cap = [spec.get("title") or "custom equations"] + [
            f"{nm}' = {e}" for nm, e in zip(["x", "y", "z", "w"], spec.get("eq", []))
        ][:3]
        if ctr is not None:
            marks.append(
                {
                    "t": "pt",
                    "f": 0,
                    "p": (
                        np.asarray(ctr, float) + np.array([-1.45, 0, 1.45]) / k
                    ).tolist(),
                    "text": "\n".join(c[:46] for c in cap),
                    "c": "#5fe3ff",
                    "a": "l",
                    "up": 0.0,
                }
            )
        for b in range(min(G.shape[1], 3)):
            marks.append(
                {
                    "t": "pt",
                    "f": 0,
                    "p": G[0, b].tolist(),
                    "text": "START" if b == 0 else f"START {b + 1}",
                    "c": "#7dffb0",
                    "a": "r",
                }
            )
        marks.append(
            {
                "t": "pt",
                "f": nf - 1,
                "p": G[-1, 0].tolist(),
                "text": "END",
                "c": "#ff9f43",
                "a": "l",
            }
        )
        if "msd" in summary and "expected_msd" in summary:
            marks.append(
                {
                    "t": "pt",
                    "f": nf - 1,
                    "p": G[-1, 0].tolist(),
                    "text": f"MSD {summary['msd']} (theory {summary['expected_msd']})",
                    "c": "#ffd24a",
                    "a": "c",
                    "up": 0.35,
                }
            )
    elif preset in ("solar_system", "three_body", "billiards"):
        for b, nm in enumerate(labels):
            if preset == "billiards" and nm != "cue":
                continue
            marks.append(
                {
                    "t": "tag",
                    "f": 0,
                    "body": b,
                    "text": nm.upper() if preset != "three_body" else nm.upper(),
                }
            )
    return marks


def build_playback(job_id: str) -> Path:
    """Resample a finished run to FPS over a watchable length and write the viewer's play file.
    Real time when the run is 4 to 14 s long, otherwise squeezed/stretched into that window."""
    z = np.load(SIMS / f"{job_id}.npz")
    meta = json.loads((SIMS / f"{job_id}.json").read_text())
    F, T = z["frames"].astype(float), z["t"]
    rad, fixed, labels = z["radii"], z["fixed"], [str(x) for x in z["labels"]]
    sim_t = float(T[-1] - T[0]) or 1.0
    play_s = float(np.clip(sim_t, 4.0, 14.0))
    nf = max(2, int(play_s * FPS))
    tt = np.linspace(T[0], T[-1], nf)
    n = F.shape[1]
    keep = np.arange(n)
    if n > 200:  # the viewer draws at most ~200 bodies: body 0 plus an even sample
        keep = np.unique(np.r_[0, np.linspace(1, n - 1, 199).astype(int)])
        F, rad, fixed = F[:, keep], rad[keep], fixed[keep]
        labels = [labels[i] for i in keep]
        n = F.shape[1]
    G = np.empty((nf, n, 3))
    for b in range(n):
        for ax in range(3):
            G[:, b, ax] = np.interp(tt, T, F[:, b, ax])
    sc = meta.get("scene", {})
    lo, hi = G.reshape(-1, 3).min(0), G.reshape(-1, 3).max(0)
    gz = sc.get("ground")
    if gz is not None:
        lo[2] = min(lo[2], gz)
    ctr, ext = (lo + hi) / 2, float((hi - lo).max()) or 1.0
    k = 3.0 / ext
    Q = ((G - ctr) * k).round(3)
    bodies = []
    pre = meta["summary"]["preset"]
    for b in range(n):
        rgb = colorsys.hsv_to_rgb((b * 0.61803) % 1.0, 0.65, 1.0)
        col = (
            "#ffd24a"
            if fixed[b]
            else "#%02x%02x%02x" % tuple(int(255 * c) for c in rgb)
        )
        r = float(np.clip(rad[b] * k, 0.06, 0.3))
        trail = True
        if pre == "brownian":
            col, trail = ("#ff9f43", True) if b == 0 else ("#3aa6c4", False)
            r = float(np.clip(rad[b] * k, 0.045, 0.3)) if b else r
        elif n > 12:
            trail = b < 12
            r = min(r, 0.07)
        bodies.append(
            {
                "label": labels[b],
                "r": round(r, 3),
                "fixed": bool(fixed[b]),
                "color": col,
                "trail": trail,
            }
        )
    box = sc.get("box")
    raw_marks = annotate(
        meta["summary"]["preset"],
        G,
        tt,
        rad,
        labels,
        gz,
        meta["summary"],
        meta.get("spec", {}),
        ctr,
        k,
    )

    def nrm(p):
        return (((np.asarray(p, float) - ctr) * k).round(3)).tolist()

    marks = []
    for m in raw_marks:
        m = dict(m)
        for key in ("p", "a", "b", "c0"):
            if key in m and not (key == "a" and m.get("t") == "pt"):
                if m.get("scene_len") and key == "b":
                    continue
                m[key] = nrm(m[key]) if key != "c0" else nrm(m[key])
        if m.get("t") == "line" and m.get("scene_len"):
            m["b"] = (
                (np.asarray(m["a"], float) + np.asarray(m["dir"], float) * m["len"])
                .round(3)
                .tolist()
            )
        marks.append(m)
    play = {
        "name": f"{meta['summary']['preset'].replace('_', ' ')}",
        "fps": FPS,
        "dur": round(play_s, 3),
        "rate": round(sim_t / play_s, 4),
        "morph": MORPH_S,
        "marks": marks,
        "bodies": bodies,
        "floor": None if gz is None else round(float((gz - ctr[2]) * k), 3),
        "box": None if box is None else round(float(box * k), 3),
        "ctr": (ctr * k).round(3).tolist(),
        "frames": Q.reshape(nf, -1).tolist(),
    }
    out = SIMS / f"{job_id}.play.json"
    out.write_text(json.dumps(play, separators=(",", ":")))
    return out


from model3d import SIM_LEAD_S as LEAD_S, SIM_MORPH_S as MORPH_S  # noqa: E402  (morph first, then the sim plays)


def present(
    job_id: str, announce: str | None = None, lead: float | None = None
) -> None:
    """Open the animated hologram for a finished run, synced with the spoken response: state.sim_start is the
    wall-clock moment frame 0 plays, set to now + lead, then the announcement goes out."""
    import model3d
    import recipes

    slug = f"sim-{job_id}"
    recipes.save(slug, {"kind": "sim", "job": job_id})
    build_playback(job_id)
    model3d.set_state(
        cmd="PROJECT",
        cmd_seq=int(model3d.get_state().get("cmd_seq") or 0) + 1,
        slug=slug,
        open=True,
        rotate=False,
        rot4d=False,
        zoom=1.0,
        wire=True,
        explode=False,
        scan=False,
        sim_start=time.time() + (LEAD_S if lead is None else lead),
    )
    if announce:
        _announce(announce, job_id)


def _wait(job_id: str, timeout: float) -> bool:
    """Wait for a job; True if it finished (done/failed). On timeout hand the announcement back to the job."""
    end = time.time() + timeout
    while time.time() < end:
        if _JOBS[job_id]["state"] != "running":
            return True
        time.sleep(0.05)
    with _LOCK:
        _JOBS[job_id]["wait"] = False
        return _JOBS[job_id]["state"] != "running"


def start(spec: dict, wait: bool = False) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "state": "running",
            "spec": spec,
            "started": time.time(),
            "wait": wait,
        }
        _ORDER.append(job_id)
    _POOL.submit(_work, job_id, spec)
    return job_id


def latest(job_id: str | None = None) -> dict | None:
    if job_id:
        return _JOBS.get(job_id)
    return _JOBS.get(_ORDER[-1]) if _ORDER else None


def trace_parts(job_id: str, max_parts: int = 380) -> tuple[str, list[dict]]:
    """Trajectory dots + final-position spheres as colour-build parts (Z up, ~3 units wide)."""
    z = np.load(SIMS / f"{job_id}.npz")
    F, rad, fixed, labels = (
        z["frames"],
        z["radii"],
        z["fixed"],
        [str(x) for x in z["labels"]],
    )
    n = F.shape[1]
    lo, hi = F.reshape(-1, 3).min(0), F.reshape(-1, 3).max(0)
    ctr, ext = (lo + hi) / 2, float((hi - lo).max()) or 1.0
    s = 3.0 / ext
    per = max(1, min(18, max_parts // max(1, n) - 1))
    idx = np.linspace(0, len(F) - 1, per).astype(int)
    parts: list[dict] = []
    for b in range(n):
        hue = (b * 0.61803) % 1.0
        rgb = colorsys.hsv_to_rgb(hue, 0.7, 1.0)
        col = "#%02x%02x%02x" % tuple(int(255 * c) for c in rgb)
        if per > 1:
            for k, fi in enumerate(idx[:-1]):
                parts.append(
                    {
                        "shape": "sphere",
                        "pos": ((F[fi, b] - ctr) * s).round(3).tolist(),
                        "size": 0.03 + 0.05 * k / per,
                        "color": col,
                    }
                )
        size = float(np.clip(rad[b] * s * 2, 0.08, 0.35)) * (1.4 if fixed[b] else 1.0)
        parts.append(
            {
                "shape": "sphere",
                "pos": ((F[-1, b] - ctr) * s).round(3).tolist(),
                "size": size,
                "color": "#ffd24a" if fixed[b] else col,
            }
        )
    return (labels[0] if labels else "simulation"), parts[:400]


class PhysicsTools:
    @property
    def tools(self) -> list:
        return [self.run_simulation, self.simulation_status, self.show_simulation]

    @function_tool()
    async def run_simulation(
        self, context: RunContext, preset: str, params: dict | None = None
    ) -> dict:
        """Run a physics simulation in the BACKGROUND with the built-in engine (returns at once; the
        result is announced when done, so keep talking meanwhile). Presets: projectile (speed m/s,
        angle deg, height, drag), bounce (n, restitution), orbit (radius AU, eccentricity),
        solar_system, three_body, pendulum (length m, angle deg), double_pendulum (angle1, angle2),
        newtons_cradle (n, lift), spring_chain (n, k), cloth (n), billiards (speed), galaxy (n),
        brownian (Brownian motion: a big pollen grain kicked by n gas molecules; n, temperature),
        random_walk / diffusion (n walkers, D), lorenz, rossler, lotka_volterra, sir, van_der_pol, lorentz_force,
        custom (bodies: [{pos, vel, mass, radius, restitution, fixed}], gravity, G, links, springs, box, duration).
        ANY OTHER phenomenon: use preset "ode" and WRITE THE EQUATIONS yourself in params, never refuse:
        {"order": 1 or 2, "eq": ["expr for dx/dt (or d2x/dt2 when order 2)", "expr for y", "expr for z"],
         "params": {"a": 1.0}, "init": {"x": "normal(0,1)", "y": 1, "z": "uniform(0,2)", "vx": 0},
         "n": number of bodies (init may use i = body index, n), "noise": {"x": "sigma expression"} for random forcing,
         "duration": s, "dt": step, "title": "what it is"}. Expressions may use x, y, z (vx, vy, vz when order 2), t, i, n, params,
        + - * / ** and sin cos tan exp log sqrt abs tanh where minimum maximum clip sign mean sum pi e. The first 3 variables are the
        x, y, z position drawn in the hologram (mean(x) gives coupling between bodies, e.g. Kuramoto, gravity-like sums).
        Use for any "simulate / what happens if / how far does it fly / orbit / swing / spread / evolve" request.

        Args:
            preset: One of the presets above.
            params: ALWAYS fill in every number Sir states, in SI units: for a bullet / projectile {"speed": metres per second, "angle": degrees above horizontal, "height": launch height m, "drag": air resistance}. Omitting them runs a default 30 m/s throw instead of his shot. Also duration (simulated seconds) and dt.
        """
        spec = {**(params or {}), "preset": preset}
        try:
            import physics

            key = str(spec["preset"]).lower().replace(" ", "_").replace("-", "_")
            k2 = physics.ALIASES.get(key, key)
            k2 = physics.BUILTIN_ALIASES.get(k2, k2)
            if k2 not in physics.PRESETS and k2 not in physics.BUILTIN_ODE:
                return {
                    "say": "That is not a built-in preset, Sir. Use preset ode with your own equations."
                }
        except Exception as e:  # noqa: BLE001
            return {"say": f"The physics engine is unavailable, Sir: {e}"}
        job_id = start(spec, wait=True)
        # quick runs (a bullet takes well under a second) answer in this same reply, so the numbers are spoken
        # and the animation starts together; slow runs hand off to the background announcement
        done = await asyncio.to_thread(_wait, job_id, 6.0)
        j = _JOBS[job_id]
        if done and j["state"] == "done":
            return {
                "say": _say(j["summary"]) + " Playing it now.",
                "job": job_id,
                "summary": j["summary"],
            }
        if done and j["state"] == "failed":
            return {
                "say": f"The simulation failed, Sir: {j.get('error')}.",
                "job": job_id,
            }
        return {
            "say": "Simulating in the background, Sir. I'll tell you the result when it's done.",
            "job": job_id,
            "background": True,
        }

    @function_tool()
    async def simulation_status(
        self, context: RunContext, job: str | None = None
    ) -> dict:
        """Status / result of the latest (or a given) physics simulation.

        Args:
            job: Optional job id from run_simulation; default is the most recent.
        """
        j = latest(job)
        if not j:
            return {"say": "No simulations have been run, Sir."}
        if j["state"] == "running":
            return {
                "say": f"Still running, Sir, {int(time.time() - j['started'])} seconds in.",
                "state": "running",
            }
        if j["state"] == "failed":
            return {
                "say": f"That simulation failed: {j.get('error')}.",
                "state": "failed",
            }
        return {
            "say": _say(j["summary"]),
            "state": "done",
            "summary": j["summary"],
            "job": j["id"],
        }

    @function_tool()
    async def show_simulation(
        self, context: RunContext, job: str | None = None
    ) -> dict:
        """Play the finished simulation as a 3D hologram ANIMATION (bodies move along their real computed
        paths with trails; real time for short runs). Also use for "replay it" / "show it again".
        It starts as Jarvis speaks.

        Args:
            job: Optional job id; default is the most recent finished one.
        """
        j = latest(job)
        if not j or j["state"] != "done":
            return {"say": "There is no finished simulation to show yet, Sir."}
        await asyncio.to_thread(present, j["id"], None, LEAD_S)
        return {"say": "Playing the simulation, Sir.", "job": j["id"]}
