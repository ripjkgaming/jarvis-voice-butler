"""Built-in physics engine for Jarvis: 3D rigid spheres / particles, run headless in the background.

World = bodies (pos, vel, mass, radius) + forces (uniform gravity, softened pairwise gravity, drag, springs)
        + rigid distance links (rods, chains, cloth; position-based) + sphere collisions + ground / box walls.
Integrator: velocity Verlet with fixed sub-steps. Everything is numpy, no extra dependencies.

    sim = simulate({"preset": "projectile", "speed": 30, "angle": 45})
    sim["summary"]    -> dict of numbers worth saying aloud
    sim["frames"]     -> (F, N, 3) trajectory,  sim["t"] -> (F,) times

Presets (see PRESETS): projectile, bounce, orbit, solar_system, three_body, pendulum, double_pendulum,
newtons_cradle, spring_chain, cloth, billiards, galaxy, custom (explicit bodies).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

G_EARTH = np.array([0.0, 0.0, -9.81])
G_ASTRO = 4 * math.pi**2  # AU^3 / (Msun yr^2)


@dataclass
class World:
    g: np.ndarray = field(default_factory=lambda: G_EARTH.copy())
    G: float = 0.0  # pairwise gravitational constant (0 = off)
    soft: float = 0.0  # gravity softening length
    drag: float = 0.0  # linear drag coefficient (force = -drag * v)
    ground: float | None = 0.0  # z of the floor (None = no floor)
    e_ground: float = 0.6  # floor restitution
    mu_ground: float = (
        0.0  # floor friction (velocity damping on contact, 0..1 per second)
    )
    box: float | None = None  # half-size of a closed box (walls) around the origin
    collide: bool = True
    pos: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    vel: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    mass: np.ndarray = field(default_factory=lambda: np.zeros(0))
    rad: np.ndarray = field(default_factory=lambda: np.zeros(0))
    rest: np.ndarray = field(default_factory=lambda: np.zeros(0))
    fixed: np.ndarray = field(default_factory=lambda: np.zeros(0, bool))
    label: list = field(default_factory=list)
    springs: list = field(default_factory=list)  # (i, j, k, rest_len, damping)
    links: list = field(default_factory=list)  # (i, j, length)
    t: float = 0.0
    collisions: int = 0

    # ---------------------------------------------------------------- build
    def add(
        self,
        pos,
        vel=(0, 0, 0),
        mass=1.0,
        radius=0.1,
        restitution=0.8,
        fixed=False,
        label="",
    ) -> int:
        self.pos = np.vstack([self.pos, np.asarray(pos, float).reshape(1, 3)])
        self.vel = np.vstack([self.vel, np.asarray(vel, float).reshape(1, 3)])
        self.mass = np.append(self.mass, float(mass))
        self.rad = np.append(self.rad, float(radius))
        self.rest = np.append(self.rest, float(restitution))
        self.fixed = np.append(self.fixed, bool(fixed))
        self.label.append(label or f"body{len(self.label)}")
        return len(self.label) - 1

    def link(self, i: int, j: int, length: float | None = None) -> None:
        L = (
            float(np.linalg.norm(self.pos[i] - self.pos[j]))
            if length is None
            else float(length)
        )
        self.links.append((i, j, L))

    def spring(
        self, i: int, j: int, k: float, rest: float | None = None, damping: float = 0.0
    ) -> None:
        r = (
            float(np.linalg.norm(self.pos[i] - self.pos[j]))
            if rest is None
            else float(rest)
        )
        self.springs.append((i, j, float(k), r, float(damping)))

    @property
    def n(self) -> int:
        return len(self.label)

    # ---------------------------------------------------------------- forces
    def accel(self, pos: np.ndarray, vel: np.ndarray) -> np.ndarray:
        n = self.n
        a = np.tile(self.g, (n, 1)).astype(float)
        if self.drag:
            a -= self.drag * vel / self.mass[:, None]
        if self.G and n > 1:
            d = pos[None, :, :] - pos[:, None, :]  # d[i, j] = pos_j - pos_i
            r2 = (d**2).sum(-1) + self.soft**2 + np.eye(n)
            inv3 = 1.0 / (r2 * np.sqrt(r2))
            np.fill_diagonal(inv3, 0.0)
            a += self.G * (d * (self.mass[None, :, None] * inv3[:, :, None])).sum(1)
        for i, j, k, r0, c in self.springs:
            d = pos[j] - pos[i]
            L = np.linalg.norm(d) + 1e-12
            u = d / L
            f = (k * (L - r0) + c * float(np.dot(vel[j] - vel[i], u))) * u
            a[i] += f / self.mass[i]
            a[j] -= f / self.mass[j]
        a[self.fixed] = 0.0
        return a

    # ---------------------------------------------------------------- step
    def step(self, dt: float) -> None:
        an = getattr(self, "analytic", None)
        if an:
            return an(self, dt) if callable(an) else _dp_rk4(self, dt)
        a0 = self.accel(self.pos, self.vel)
        prev = self.pos.copy()
        self.pos = self.pos + self.vel * dt + 0.5 * a0 * dt * dt
        vp = self.vel + a0 * dt
        a1 = self.accel(self.pos, vp)
        self.vel = self.vel + 0.5 * (a0 + a1) * dt
        self.vel[self.fixed] = 0.0
        self.pos[self.fixed] = prev[self.fixed]
        if self.links:
            self._solve_links(dt)
        if self.collide and self.n > 1:
            self._collide()
        self._walls(dt)
        self.t += dt

    def _solve_links_fast(self) -> None:
        """Vectorised Jacobi relaxation for big link sets (cloth, nets)."""
        if not hasattr(self, "_L"):
            self._L = np.array([(i, j) for i, j, _ in self.links])
            self._len = np.array([L for _, _, L in self.links])
            self._deg = (
                np.bincount(self._L.ravel(), minlength=self.n).astype(float).clip(1)
            )
        i, j = self._L[:, 0], self._L[:, 1]
        w = np.where(self.fixed, 0.0, 1.0 / self.mass)
        wsum = (w[i] + w[j])[:, None]
        ok = wsum[:, 0] > 0
        for _ in range(30):
            d = self.pos[j] - self.pos[i]
            dist = np.linalg.norm(d, axis=1, keepdims=True) + 1e-12
            corr = np.where(
                ok[:, None],
                (dist - self._len[:, None]) / dist / np.where(wsum > 0, wsum, 1) * d,
                0.0,
            )
            delta = np.zeros_like(self.pos)
            np.add.at(delta, i, w[i][:, None] * corr)
            np.add.at(delta, j, -w[j][:, None] * corr)
            self.pos += delta / self._deg[:, None] * 1.6
        for _ in range(8):
            d = self.pos[j] - self.pos[i]
            u = d / (np.linalg.norm(d, axis=1, keepdims=True) + 1e-12)
            rv = ((self.vel[j] - self.vel[i]) * u).sum(1, keepdims=True)
            imp = np.where(ok[:, None], rv / np.where(wsum > 0, wsum, 1), 0.0) * u
            dv = np.zeros_like(self.vel)
            np.add.at(dv, i, w[i][:, None] * imp)
            np.add.at(dv, j, -w[j][:, None] * imp)
            self.vel += dv / self._deg[:, None] * 1.6

    def _solve_links(self, dt: float, iters: int = 12) -> None:
        if len(self.links) > 12:
            return self._solve_links_fast()
        w = np.where(self.fixed, 0.0, 1.0 / self.mass)
        for _ in range(iters):
            for i, j, L in self.links:
                d = self.pos[j] - self.pos[i]
                dist = np.linalg.norm(d) + 1e-12
                wi, wj = w[i], w[j]
                if wi + wj == 0:
                    continue
                corr = (dist - L) / dist / (wi + wj) * d
                self.pos[i] += wi * corr
                self.pos[j] -= wj * corr
        for _ in range(4):  # remove velocity along each rod
            for i, j, L in self.links:
                d = self.pos[j] - self.pos[i]
                u = d / (np.linalg.norm(d) + 1e-12)
                wi, wj = w[i], w[j]
                if wi + wj == 0:
                    continue
                rv = float(np.dot(self.vel[j] - self.vel[i], u))
                imp = rv / (wi + wj)
                self.vel[i] += wi * imp * u
                self.vel[j] -= wj * imp * u

    def _collide(self) -> None:
        n = self.n
        if n > 800:
            return
        iu, ju = np.triu_indices(n, 1)
        d = self.pos[ju] - self.pos[iu]
        dist = np.linalg.norm(d, axis=1)
        hit = np.nonzero(dist < (self.rad[iu] + self.rad[ju]))[0]
        linked = {(min(i, j), max(i, j)) for i, j, _ in self.links}
        for h in hit:
            i, j = int(iu[h]), int(ju[h])
            if (i, j) in linked:
                continue
            dd = self.pos[j] - self.pos[i]
            L = float(np.linalg.norm(dd)) + 1e-12
            u = dd / L
            wi = 0.0 if self.fixed[i] else 1.0 / self.mass[i]
            wj = 0.0 if self.fixed[j] else 1.0 / self.mass[j]
            if wi + wj == 0:
                continue
            pen = self.rad[i] + self.rad[j] - L
            self.pos[i] -= u * pen * wi / (wi + wj)
            self.pos[j] += u * pen * wj / (wi + wj)
            rv = float(np.dot(self.vel[j] - self.vel[i], u))
            if rv < 0:
                e = min(self.rest[i], self.rest[j])
                imp = -(1 + e) * rv / (wi + wj)
                self.vel[i] -= imp * wi * u
                self.vel[j] += imp * wj * u
                self.collisions += 1

    def _walls(self, dt: float) -> None:
        free = ~self.fixed
        if self.ground is not None:
            low = free & (self.pos[:, 2] - self.rad < self.ground)
            if low.any():
                self.pos[low, 2] = self.ground + self.rad[low]
                vz = self.vel[low, 2]
                bounce = vz < 0
                if bounce.any():
                    idx = np.nonzero(low)[0][bounce]
                    self.collisions += int(
                        (self.vel[idx, 2] < -0.5).sum()
                    )  # real impacts only
                    self.vel[idx, 2] = -self.vel[idx, 2] * np.minimum(
                        self.rest[idx], self.e_ground
                    )
                    # tiny bounces settle to rest
                    rest = np.abs(self.vel[idx, 2]) < 0.2
                    self.vel[idx[rest], 2] = 0.0
                if self.mu_ground:
                    self.vel[low, :2] *= max(0.0, 1.0 - self.mu_ground * dt)
        if self.box is not None:
            for ax in range(3 if self.ground is None else 2):
                hi = free & (self.pos[:, ax] + self.rad > self.box)
                lo = free & (self.pos[:, ax] - self.rad < -self.box)
                for m, sgn in ((hi, -1), (lo, 1)):
                    if m.any():
                        self.pos[m, ax] = (
                            (self.box - self.rad[m])
                            if sgn < 0
                            else (-self.box + self.rad[m])
                        )
                        self.vel[m, ax] = -self.vel[m, ax] * self.rest[m]
                        self.collisions += int(m.sum())

    # ---------------------------------------------------------------- diagnostics
    def energy(self) -> float:
        ke = 0.5 * float((self.mass * (self.vel**2).sum(1)).sum())
        pe = -float((self.mass * (self.pos @ self.g)).sum())
        if self.G and self.n > 1:
            for i in range(self.n):
                for j in range(i + 1, self.n):
                    r = math.sqrt(
                        float(((self.pos[i] - self.pos[j]) ** 2).sum()) + self.soft**2
                    )
                    pe -= self.G * self.mass[i] * self.mass[j] / r
        for i, j, k, r0, c in self.springs:
            L = float(np.linalg.norm(self.pos[i] - self.pos[j]))
            pe += 0.5 * k * (L - r0) ** 2
        return ke + pe

    def momentum(self) -> np.ndarray:
        return (self.mass[:, None] * self.vel).sum(0)


# -------------------------------------------------------------------- runner
def run(
    w: World,
    duration: float,
    dt: float = 1e-3,
    max_frames: int = 1500,
    stop=None,
    wall_cap: float = 150.0,
) -> dict:
    """Step the world for `duration`, recording up to max_frames frames. `stop()` -> True aborts."""
    steps = max(1, int(round(duration / dt)))
    every = max(1, steps // max_frames)
    frames, times = [w.pos.copy()], [w.t]
    e0 = w.energy()
    vmax = 0.0
    t0 = time.time()
    for k in range(1, steps + 1):
        w.step(dt)
        if k % every == 0:
            frames.append(w.pos.copy())
            times.append(w.t)
            vmax = max(vmax, float(np.linalg.norm(w.vel, axis=1).max()))
            if (stop and stop()) or time.time() - t0 > wall_cap:
                break
    e1 = w.energy()
    return {
        "frames": np.array(frames),
        "t": np.array(times),
        "e0": e0,
        "e1": e1,
        "vmax": vmax,
        "collisions": w.collisions,
        "wall_s": time.time() - t0,
        "steps": k,
    }


# -------------------------------------------------------------------- presets
def _projectile(p):
    w = World(drag=float(p.get("drag", 0.0)), ground=0.0, e_ground=0.0)
    s, ang = float(p.get("speed", 30)), math.radians(float(p.get("angle", 45)))
    w.add(
        (0, 0, float(p.get("height", 0)) + 0.1),
        (s * math.cos(ang), 0, s * math.sin(ang)),
        mass=float(p.get("mass", 1)),
        radius=0.1,
        restitution=0.0,
        label="projectile",
    )
    return w, float(p.get("duration", 2 * s * math.sin(ang) / 9.81 + 2)), 1e-3


def _bounce(p):
    w = World(
        ground=0.0,
        e_ground=float(p.get("restitution", 0.8)),
        box=float(p.get("box", 5)),
    )
    rng = np.random.default_rng(int(p.get("seed", 1)))
    for i in range(int(p.get("n", 12))):
        w.add(
            rng.uniform(-4, 4, 3) * [1, 1, 0] + [0, 0, rng.uniform(1, 8)],
            rng.normal(0, 2, 3),
            mass=1,
            radius=float(p.get("radius", 0.4)),
            restitution=float(p.get("restitution", 0.8)),
            label=f"ball{i}",
        )
    return w, float(p.get("duration", 10)), 2e-3


def _orbit(p):
    w = World(g=np.zeros(3), G=G_ASTRO, ground=None, collide=False)
    r, e = float(p.get("radius", 1.0)), float(p.get("eccentricity", 0.0))
    w.add((0, 0, 0), (0, 0, 0), mass=1.0, radius=0.05, fixed=True, label="sun")
    v = 2 * math.pi * math.sqrt((1 - e) / r) if e else 2 * math.pi / math.sqrt(r)
    w.add((r, 0, 0), (0, v, 0), mass=3e-6, radius=0.02, label="planet")
    return w, float(p.get("duration", 2 * math.pi * r**1.5 * 2)), 2e-4


def _solar(p):
    w = World(g=np.zeros(3), G=G_ASTRO, ground=None, collide=False)
    w.add((0, 0, 0), (0, 0, 0), mass=1.0, radius=0.05, label="sun")
    planets = [
        ("mercury", 0.387, 1.66e-7),
        ("venus", 0.723, 2.45e-6),
        ("earth", 1.0, 3.0e-6),
        ("mars", 1.524, 3.2e-7),
        ("jupiter", 5.203, 9.55e-4),
        ("saturn", 9.537, 2.86e-4),
        ("uranus", 19.19, 4.4e-5),
        ("neptune", 30.07, 5.15e-5),
    ]
    for k, (nm, a, m) in enumerate(planets):
        ang = k * 0.9
        v = 2 * math.pi / math.sqrt(a)
        w.add(
            (a * math.cos(ang), a * math.sin(ang), 0),
            (-v * math.sin(ang), v * math.cos(ang), 0),
            mass=m,
            radius=0.02,
            label=nm,
        )
    com = (w.mass[:, None] * w.vel).sum(0) / w.mass.sum()
    w.vel -= com
    return w, float(p.get("duration", 12.0)), 5e-4


def _three_body(p):
    w = World(g=np.zeros(3), G=1.0, ground=None, collide=False)
    p1, v3 = (
        np.array([0.97000436, -0.24308753, 0.0]),
        np.array([-0.93240737, -0.86473146, 0.0]),
    )
    w.add(p1, -v3 / 2, label="a")
    w.add(-p1, -v3 / 2, label="b")
    w.add((0, 0, 0), v3, label="c")
    return w, float(p.get("duration", 6.3259 * 2)), 1e-4


def _pendulum(p):
    L, th = float(p.get("length", 1.0)), math.radians(float(p.get("angle", 20)))
    w = World(ground=None, collide=False)
    a = w.add((0, 0, 0), fixed=True, radius=0.02, label="pivot")
    b = w.add(
        (L * math.sin(th), 0, -L * math.cos(th)), mass=1.0, radius=0.05, label="bob"
    )
    w.link(a, b, L)
    return w, float(p.get("duration", 10)), 5e-4


def _double_pendulum(p):
    """Exact Lagrangian ODE integrated with RK4 (a rod-constraint solver drifts on chaotic motion)."""
    w, dur, dt = _double_pendulum_world(p)
    return w, dur, dt


def _double_pendulum_world(p):
    L1, L2 = float(p.get("l1", 1.0)), float(p.get("l2", 1.0))
    a1, a2 = (
        math.radians(float(p.get("angle1", 120))),
        math.radians(float(p.get("angle2", -10))),
    )
    w = World(ground=None, collide=False)
    o = w.add((0, 0, 0), fixed=True, radius=0.02, label="pivot")
    x1 = np.array([L1 * math.sin(a1), 0, -L1 * math.cos(a1)])
    b1 = w.add(x1, mass=1.0, radius=0.05, label="bob1")
    b2 = w.add(
        x1 + [L2 * math.sin(a2), 0, -L2 * math.cos(a2)],
        mass=1.0,
        radius=0.05,
        label="bob2",
    )
    w.analytic = ("double_pendulum", L1, L2, a1, a2)
    return w, float(p.get("duration", 20)), 1e-3


def _dp_rk4(w: "World", dt: float) -> None:
    _, L1, L2, _, _ = w.analytic
    g, m1, m2 = 9.81, w.mass[1], w.mass[2]
    th = getattr(w, "_th", None)
    if th is None:
        th = np.array([w.analytic[3], w.analytic[4], 0.0, 0.0])

    def f(y):
        t1, t2, o1, o2 = y
        d = t1 - t2
        den = 2 * m1 + m2 - m2 * math.cos(2 * d)
        a1 = (
            -g * (2 * m1 + m2) * math.sin(t1)
            - m2 * g * math.sin(t1 - 2 * t2)
            - 2 * math.sin(d) * m2 * (o2 * o2 * L2 + o1 * o1 * L1 * math.cos(d))
        ) / (L1 * den)
        a2 = (
            2
            * math.sin(d)
            * (
                o1 * o1 * L1 * (m1 + m2)
                + g * (m1 + m2) * math.cos(t1)
                + o2 * o2 * L2 * m2 * math.cos(d)
            )
        ) / (L2 * den)
        return np.array([o1, o2, a1, a2])

    k1 = f(th)
    k2 = f(th + dt / 2 * k1)
    k3 = f(th + dt / 2 * k2)
    k4 = f(th + dt * k3)
    th = th + dt / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    w._th = th
    x1 = np.array([L1 * math.sin(th[0]), 0, -L1 * math.cos(th[0])])
    w.pos[1] = x1
    w.pos[2] = x1 + [L2 * math.sin(th[1]), 0, -L2 * math.cos(th[1])]
    w.vel[1] = [L1 * th[2] * math.cos(th[0]), 0, L1 * th[2] * math.sin(th[0])]
    w.vel[2] = w.vel[1] + [
        L2 * th[3] * math.cos(th[1]),
        0,
        L2 * th[3] * math.sin(th[1]),
    ]
    w.t += dt


def _cradle(p):
    n, L, r = int(p.get("n", 5)), 1.0, 0.1
    w = World(ground=None, collide=True)
    th = math.radians(float(p.get("angle", 35)))
    for i in range(n):
        x = i * 2 * r * 1.001
        piv = w.add((x, 0, 0), fixed=True, radius=0.01, label=f"pivot{i}")
        if i < int(p.get("lift", 1)):
            pos = (x - L * math.sin(th), 0, -L * math.cos(th))
        else:
            pos = (x, 0, -L)
        b = w.add(pos, mass=1.0, radius=r, restitution=1.0, label=f"ball{i}")
        w.link(piv, b, L)
    return w, float(p.get("duration", 6)), 2e-4


def _spring_chain(p):
    n, k = int(p.get("n", 8)), float(p.get("k", 200))
    w = World(ground=None, collide=False, drag=float(p.get("drag", 0.05)))
    prev = w.add((0, 0, 0), fixed=True, radius=0.03, label="anchor")
    for i in range(1, n + 1):
        b = w.add((0.3 * i, 0.0, 0.0), mass=0.2, radius=0.04, label=f"m{i}")
        w.spring(prev, b, k, 0.3, 0.2)
        prev = b
    return w, float(p.get("duration", 6)), 5e-4


def _cloth(p):
    n = int(p.get("n", 12))
    w = World(
        ground=float(p.get("floor", -2.0)),
        collide=False,
        drag=0.1,
        e_ground=0.0,
        mu_ground=2.0,
    )
    ids = {}
    s = 0.15
    for i in range(n):
        for j in range(n):
            ids[i, j] = w.add(
                (i * s - n * s / 2, j * s - n * s / 2, 0.0),
                mass=0.05,
                radius=0.03,
                fixed=(j == n - 1 and i in (0, n - 1)),
                label=f"c{i}_{j}",
            )
    for i in range(n):
        for j in range(n):
            if i + 1 < n:
                w.link(ids[i, j], ids[i + 1, j], s)
            if j + 1 < n:
                w.link(ids[i, j], ids[i, j + 1], s)
    return w, float(p.get("duration", 4)), 1e-3


def _billiards(p):
    w = World(g=np.zeros(3), ground=None, box=1.0, collide=True)
    w.box = 1.0
    r = 0.057 / 2 * 6
    w.add(
        (-0.6, 0.0, 0.0),
        (float(p.get("speed", 3.0)), 0.15, 0.0),
        mass=0.17,
        radius=r,
        restitution=0.95,
        label="cue",
    )
    row = 0
    for i in range(5):
        for j in range(i + 1):
            w.add(
                (0.2 + i * r * 1.8, (j - i / 2) * r * 2.05, 0.0),
                mass=0.16,
                radius=r,
                restitution=0.95,
                label=f"ball{row}",
            )
            row += 1
    return w, float(p.get("duration", 5)), 5e-4


def _galaxy(p):
    n = int(p.get("n", 60))
    rng = np.random.default_rng(int(p.get("seed", 2)))
    w = World(g=np.zeros(3), G=1.0, soft=0.05, ground=None, collide=False)
    w.add((0, 0, 0), (0, 0, 0), mass=float(n), radius=0.1, label="core")
    for i in range(n):
        r = rng.uniform(0.5, 3.0)
        a = rng.uniform(0, 2 * math.pi)
        v = math.sqrt(n / r)
        w.add(
            (r * math.cos(a), r * math.sin(a), rng.normal(0, 0.05)),
            (-v * math.sin(a), v * math.cos(a), 0),
            mass=0.01,
            radius=0.03,
            label=f"star{i}",
        )
    return w, float(p.get("duration", 6)), 2e-3


def _custom(p):
    w = World(
        g=np.array(p.get("gravity", [0, 0, -9.81]), float),
        G=float(p.get("G", 0)),
        soft=float(p.get("soft", 0)),
        drag=float(p.get("drag", 0)),
        ground=p.get("ground", 0.0),
        e_ground=float(p.get("e_ground", 0.6)),
        box=p.get("box"),
        collide=bool(p.get("collide", True)),
    )
    for b in p.get("bodies", [])[:2000]:
        w.add(
            b.get("pos", [0, 0, 0]),
            b.get("vel", [0, 0, 0]),
            b.get("mass", 1),
            b.get("radius", 0.1),
            b.get("restitution", 0.8),
            b.get("fixed", False),
            b.get("label", ""),
        )
    for l in p.get("links", []):
        w.link(int(l[0]), int(l[1]), l[2] if len(l) > 2 else None)
    for s in p.get("springs", []):
        w.spring(
            int(s[0]),
            int(s[1]),
            float(s[2]),
            s[3] if len(s) > 3 else None,
            s[4] if len(s) > 4 else 0.0,
        )
    return w, float(p.get("duration", 5)), float(p.get("dt", 1e-3))


# ------------------------------------------------------------ general ODE / SDE engine ("any simulation")
import ast

_FUNCS = {
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "exp": np.exp,
    "log": np.log,
    "sqrt": np.sqrt,
    "abs": np.abs,
    "tanh": np.tanh,
    "sinh": np.sinh,
    "cosh": np.cosh,
    "arctan": np.arctan,
    "arctan2": np.arctan2,
    "arcsin": np.arcsin,
    "arccos": np.arccos,
    "where": np.where,
    "minimum": np.minimum,
    "maximum": np.maximum,
    "clip": np.clip,
    "sign": np.sign,
    "mean": np.mean,
    "sum": np.sum,
    "floor": np.floor,
    "mod": np.mod,
    "heaviside": lambda a: (np.asarray(a) > 0) * 1.0,
}
_CONST = {"pi": math.pi, "e": math.e}
_OK_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Call,
    ast.Compare,
    ast.IfExp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.USub,
    ast.UAdd,
    ast.Lt,
    ast.Gt,
    ast.LtE,
    ast.GtE,
    ast.Eq,
    ast.NotEq,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.Not,
    ast.FloorDiv,
)


def compile_expr(src, allowed: set, funcs: dict):
    """Safe vectorised expression: arithmetic + whitelisted numpy functions on whitelisted names only."""
    if isinstance(src, (int, float)):
        v = float(src)
        return lambda env: v
    src = str(src).strip().replace("^", "**")
    if len(src) > 400:
        raise ValueError("expression too long")
    tree = ast.parse(src, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _OK_NODES):
            raise ValueError(f"not allowed in an expression: {type(node).__name__}")
        if (
            isinstance(node, ast.Name)
            and node.id not in allowed
            and node.id not in funcs
            and node.id not in _CONST
        ):
            raise ValueError(f"unknown name {node.id!r} in {src!r}")
        if isinstance(node, ast.Call) and not (
            isinstance(node.func, ast.Name) and node.func.id in funcs
        ):
            raise ValueError("only whitelisted functions can be called")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("only numbers allowed")
    code = compile(tree, "<expr>", "eval")
    return lambda env: eval(code, {"__builtins__": {}}, {**funcs, **_CONST, **env})  # noqa: S307  (AST-whitelisted)


BUILTIN_ODE = {
    "lorenz": {
        "order": 1,
        "n": 2,
        "eq": ["sigma*(y-x)", "x*(rho-z)-y", "x*y-beta*z"],
        "params": {"sigma": 10, "rho": 28, "beta": 8 / 3},
        "init": {"x": "1+i*0.001", "y": 1, "z": 1},
        "duration": 30,
        "dt": 0.004,
        "title": "Lorenz attractor (two nearly identical starts diverge)",
    },
    "rossler": {
        "order": 1,
        "n": 1,
        "eq": ["-y-z", "x+a*y", "b+z*(x-c)"],
        "params": {"a": 0.2, "b": 0.2, "c": 5.7},
        "init": {"x": 1, "y": 1, "z": 0},
        "duration": 120,
        "dt": 0.01,
        "title": "Rossler attractor",
    },
    "lotka_volterra": {
        "order": 1,
        "n": 4,
        "eq": ["a*x-b*x*y", "d*x*y-c*y", "0"],
        "params": {"a": 1.0, "b": 0.5, "c": 0.75, "d": 0.25},
        "init": {"x": "2+i*1.5", "y": 2, "z": 0},
        "duration": 40,
        "dt": 0.005,
        "title": "Predator-prey (x prey, y predator)",
    },
    "sir": {
        "order": 1,
        "n": 1,
        "eq": ["-beta*x*y", "beta*x*y-gamma*y", "gamma*y"],
        "params": {"beta": 0.4, "gamma": 0.1},
        "init": {"x": 0.99, "y": 0.01, "z": 0},
        "duration": 120,
        "dt": 0.05,
        "title": "SIR epidemic (x susceptible, y infected, z recovered)",
    },
    "van_der_pol": {
        "order": 1,
        "n": 3,
        "eq": ["y", "mu*(1-x*x)*y-x", "0"],
        "params": {"mu": 2.5},
        "init": {"x": "0.5+i", "y": 0, "z": 0},
        "duration": 40,
        "dt": 0.005,
        "title": "Van der Pol oscillator limit cycle",
    },
    "lorentz_force": {
        "order": 2,
        "n": 1,
        "eq": ["q*(vy*B)/m", "q*(-vx*B)/m + 0*E", "0"],
        "params": {"q": 1, "m": 1, "B": 2, "E": 0},
        "init": {"x": 0, "y": 0, "z": 0, "vx": 1, "vy": 0, "vz": 0.3},
        "duration": 12,
        "dt": 0.002,
        "title": "Charged particle in a magnetic field",
    },
    "random_walk": {
        "order": 1,
        "n": 40,
        "eq": ["0", "0", "0"],
        "params": {"D": 0.5},
        "noise": {"x": "sqrt(2*D)", "y": "sqrt(2*D)", "z": "sqrt(2*D)"},
        "init": {"x": 0, "y": 0, "z": 0},
        "duration": 10,
        "dt": 0.005,
        "title": "Diffusion: independent random walkers",
    },
}


class OdeWorld(World):
    """World whose dynamics are user-written equations (first-order dX/dt = f, or second-order d2X/dt2 = f) with optional noise."""

    pass


def _ode(p):
    base = dict(BUILTIN_ODE.get(str(p.get("builtin", "")), {}))
    spec = {**base, **{k: v for k, v in p.items() if k not in ("preset", "builtin")}}
    order = int(spec.get("order", 1))
    n = int(max(1, min(int(spec.get("n", 1)), 2000)))
    eqs = list(spec.get("eq") or [])
    if not eqs:
        raise ValueError("ode needs eq: one expression per variable")
    nv = len(eqs)
    names = list(spec.get("vars") or ["x", "y", "z", "w"][:nv])
    if len(names) < nv:
        names = names + [f"u{k}" for k in range(len(names), nv)]
    names = names[:nv]
    vel = [("v" + nm) for nm in names] if order == 2 else []
    state_names = names + vel
    params = {str(k): float(v) for k, v in (spec.get("params") or {}).items()}
    allowed = set(state_names) | set(params) | {"t", "i", "n"}
    funcs = dict(_FUNCS)
    fe = [compile_expr(e, allowed, funcs) for e in eqs]
    noise = spec.get("noise") or {}
    ne = {nm: compile_expr(noise[nm], allowed, funcs) for nm in noise if nm in names}
    ifuncs = {
        **funcs,
        "normal": lambda mu=0.0, sg=1.0: np.random.default_rng(
            int(spec.get("seed", 1))
        ).normal(mu, sg, n),
        "uniform": lambda lo=0.0, hi=1.0: np.random.default_rng(
            int(spec.get("seed", 1)) + 7
        ).uniform(lo, hi, n),
    }
    idx = np.arange(n, dtype=float)
    init = spec.get("init") or {}
    Y = np.zeros((n, len(state_names)))
    if isinstance(init, list):
        for r, row in enumerate(init[:n]):
            Y[r, : len(row)] = row
    else:
        for j, nm in enumerate(state_names):
            if nm in init:
                f = compile_expr(init[nm], params.keys() | {"i", "n"}, ifuncs)
                Y[:, j] = np.broadcast_to(f({**params, "i": idx, "n": float(n)}), (n,))
    dur, dt = float(spec.get("duration", 10)), float(spec.get("dt", 1e-3))
    w = OdeWorld(g=np.zeros(3), ground=None, collide=False)
    for k in range(n):
        w.add(
            Y[k, :3] if nv >= 3 else np.r_[Y[k, :nv], np.zeros(3 - nv)],
            radius=0.06,
            label=f"body{k}",
        )
    w.ode_meta = {
        "names": state_names,
        "order": order,
        "title": spec.get("title") or "custom equations",
        "eqs": eqs,
        "n": n,
        "noise": bool(ne),
        "params": params,
        "spec": spec,
    }
    rng = np.random.default_rng(int(spec.get("seed", 1)) + 99)
    state = {"Y": Y, "t": 0.0}
    ns = len(state_names)
    nvars = nv

    def deriv(Yc, t):
        env = {nm: Yc[:, j] for j, nm in enumerate(state_names)}
        env.update(params)
        env.update({"t": t, "i": idx, "n": float(n)})
        out = np.zeros_like(Yc)
        vals = [np.broadcast_to(np.asarray(f(env), float), (n,)) for f in fe]
        if order == 1:
            for j in range(nvars):
                out[:, j] = vals[j]
        else:
            for j in range(nvars):
                out[:, j] = Yc[:, nvars + j]
                out[:, nvars + j] = vals[j]
        return out

    def step(world, h):
        Yc, t = state["Y"], state["t"]
        k1 = deriv(Yc, t)
        k2 = deriv(Yc + 0.5 * h * k1, t + 0.5 * h)
        k3 = deriv(Yc + 0.5 * h * k2, t + 0.5 * h)
        k4 = deriv(Yc + h * k3, t + h)
        Yn = Yc + h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
        if ne:
            env = {nm: Yc[:, j] for j, nm in enumerate(state_names)}
            env.update(params)
            env.update({"t": t, "i": idx, "n": float(n)})
            for nm, f in ne.items():
                Yn[:, state_names.index(nm)] += (
                    np.broadcast_to(np.asarray(f(env), float), (n,))
                    * math.sqrt(h)
                    * rng.normal(size=n)
                )
        if not np.isfinite(Yn).all():
            raise ValueError(
                "the equations blew up (non-finite values); try a smaller dt or different parameters"
            )
        state["Y"], state["t"] = Yn, t + h
        world.pos[:, : min(3, nvars)] = Yn[:, : min(3, nvars)]
        d = deriv(Yn, t + h)
        world.vel[:, : min(3, nvars)] = (
            d[:, : min(3, nvars)]
            if order == 1
            else Yn[:, nvars : nvars + min(3, nvars)]
        )
        world.t += h

    w.analytic = step
    return w, dur, dt


def _brownian(p):
    """Hard-sphere gas in a box with one big tracer: the tracer's jittery random walk IS Brownian motion."""
    n = int(max(10, min(int(p.get("n", 150)), 600)))
    T = float(p.get("temperature", 1.0))
    box = float(p.get("box", 3.0))
    R = float(p.get("grain_radius", 0.45))
    M = float(p.get("grain_mass", 40.0))
    w = World(g=np.zeros(3), ground=None, box=box, collide=True)
    rng = np.random.default_rng(int(p.get("seed", 3)))
    w.add((0, 0, 0), (0, 0, 0), mass=M, radius=R, restitution=1.0, label="pollen grain")
    sg = math.sqrt(T)
    placed = 0
    while placed < n:
        q = rng.uniform(-box + 0.15, box - 0.15, 3)
        if np.linalg.norm(q) < R + 0.2 or any(
            np.linalg.norm(q - w.pos[j]) < 0.2 for j in range(1, w.n)
        ):
            continue
        w.add(
            q,
            rng.normal(0, 2.0 * sg, 3),
            mass=1.0,
            radius=0.08,
            restitution=1.0,
            label=f"molecule{placed}",
        )
        placed += 1
    return w, float(p.get("duration", 14)), float(p.get("dt", 2e-3))


PRESETS = {
    "projectile": _projectile,
    "bounce": _bounce,
    "orbit": _orbit,
    "solar_system": _solar,
    "three_body": _three_body,
    "pendulum": _pendulum,
    "double_pendulum": _double_pendulum,
    "newtons_cradle": _cradle,
    "spring_chain": _spring_chain,
    "cloth": _cloth,
    "billiards": _billiards,
    "galaxy": _galaxy,
    "custom": _custom,
    "brownian": _brownian,
    "ode": _ode,
}
BUILTIN_ALIASES = {
    "lorenz_attractor": "lorenz",
    "butterfly_effect": "lorenz",
    "chaos_attractor": "lorenz",
    "predator_prey": "lotka_volterra",
    "lotka": "lotka_volterra",
    "epidemic": "sir",
    "disease": "sir",
    "vdp": "van_der_pol",
    "cyclotron": "lorentz_force",
    "magnetic_field": "lorentz_force",
    "diffusion": "random_walk",
    "random_walker": "random_walk",
    "walk": "random_walk",
}
ALIASES = {
    "brownian_motion": "brownian",
    "pollen": "brownian",
    "gas": "brownian",
    "equations": "ode",
    "equation": "ode",
    "differential_equation": "ode",
    "ball": "projectile",
    "throw": "projectile",
    "cannon": "projectile",
    "bouncing_balls": "bounce",
    "balls": "bounce",
    "planet": "orbit",
    "two_body": "orbit",
    "solar": "solar_system",
    "planets": "solar_system",
    "figure_eight": "three_body",
    "3_body": "three_body",
    "chaos": "double_pendulum",
    "cradle": "newtons_cradle",
    "spring": "spring_chain",
    "springs": "spring_chain",
    "pool": "billiards",
    "snooker": "billiards",
    "n_body": "galaxy",
    "nbody": "galaxy",
}


def summarize(preset: str, w: World, out: dict, params: dict) -> dict:
    F, T = out["frames"], out["t"]
    s: dict = {
        "preset": preset,
        "bodies": w.n,
        "sim_time_s": round(float(T[-1]), 4),
        "frames": int(len(T)),
        "wall_s": round(out["wall_s"], 3),
        "collisions": int(out["collisions"]),
    }
    e0, e1 = out["e0"], out["e1"]
    if abs(e0) > 1e-12 and not w.drag and not w.collisions and not w.links:
        s["energy_drift"] = float(f"{abs(e1 - e0) / abs(e0):.3e}")
    if preset == "projectile":
        z = F[:, 0, 2]
        land = (
            int(np.argmax((z <= 0.11) & (T > 0.05)))
            if (z <= 0.11).any() and (T > 0.05).any()
            else len(T) - 1
        )
        s.update(
            max_height_m=round(float(z.max() - 0.1), 3),
            range_m=round(float(F[land, 0, 0]), 3),
            flight_time_s=round(float(T[land]), 3),
        )
    elif preset in ("pendulum",):
        x = F[:, 1, 0]
        cross = np.nonzero(np.diff(np.sign(x)) != 0)[0]
        if len(cross) >= 3:
            s["period_s"] = round(
                float(2 * (T[cross[-1]] - T[cross[0]]) / (len(cross) - 1)), 4
            )
    elif preset == "orbit":
        r = np.linalg.norm(F[:, 1] - F[:, 0], axis=1)
        s.update(
            min_distance_au=round(float(r.min()), 4),
            max_distance_au=round(float(r.max()), 4),
        )
    elif preset == "brownian":
        gp = F[:, 0]
        step = np.linalg.norm(np.diff(gp, axis=0), axis=1)
        lag = max(1, len(gp) // 4)
        msd = float(np.mean(np.sum((gp[lag:] - gp[:-lag]) ** 2, axis=1)))
        tau = float(T[lag] - T[0])
        s.update(
            net_displacement=round(float(np.linalg.norm(gp[-1] - gp[0])), 3),
            path_length=round(float(step.sum()), 3),
            msd=round(msd, 4),
            diffusion_coeff=round(msd / (6 * tau), 4),
            lag_s=round(tau, 3),
            molecules=w.n - 1,
        )
    elif preset == "ode":
        meta = w.ode_meta
        k3 = min(
            3, len(meta["names"]) if meta["order"] == 1 else len(meta["names"]) // 2
        )
        s.update(
            equations=meta["title"],
            variables=meta["names"][:k3],
            final_mean=[round(float(x), 4) for x in F[-1, :, :k3].mean(0)],
            spread=[round(float(x), 4) for x in F[-1, :, :k3].std(0)],
        )
        if meta["noise"] and w.n > 1:
            disp = F - F[0:1]
            msd = float(np.mean(np.sum(disp[-1, :, :k3] ** 2, axis=1)))
            tt = float(T[-1] - T[0])
            s.update(msd=round(msd, 4), diffusion_coeff=round(msd / (2 * k3 * tt), 4))
            if "D" in meta["params"]:
                s["expected_msd"] = round(2 * k3 * meta["params"]["D"] * tt, 4)
    elif preset == "three_body":
        s["max_extent"] = round(float(np.abs(F).max()), 3)
    elif preset == "solar_system":
        i = w.label.index("earth")
        r = np.linalg.norm(F[:, i] - F[:, 0], axis=1)
        s["earth_distance_au_range"] = [
            round(float(r.min()), 3),
            round(float(r.max()), 3),
        ]
    s["max_speed"] = round(float(out["vmax"]), 3)
    return s


def simulate(spec: dict, stop=None) -> dict:
    """spec = {"preset": name, ...params}. Returns {"summary","frames","t","labels","radii"}."""
    preset = (
        str(spec.get("preset", "custom")).lower().replace(" ", "_").replace("-", "_")
    )
    preset = ALIASES.get(preset, preset)
    preset = BUILTIN_ALIASES.get(preset, preset)
    if preset in BUILTIN_ODE:
        spec = {**spec, "builtin": preset}
        preset = "ode"
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}; choose from {', '.join(PRESETS)}")
    w, dur, dt = PRESETS[preset](spec)
    dur = float(spec.get("duration", dur))
    dt = float(spec.get("dt", dt))
    dur = min(dur, 1e5)
    steps = dur / dt
    if steps > 3e6:
        dt = dur / 3e6  # bound the work
    out = run(w, dur, dt, int(spec.get("max_frames", 1500)), stop)
    return {
        "summary": summarize(preset, w, out, spec),
        "frames": out["frames"],
        "t": out["t"],
        "labels": list(w.label),
        "radii": w.rad.copy(),
        "fixed": w.fixed.copy(),
        "scene": {"ground": w.ground, "box": w.box, "preset": preset},
    }
