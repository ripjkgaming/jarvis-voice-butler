"""Procedural 3D shapes (catalog tag P): mesh built in numpy, saved as GLB. No Blender.

Usage: python3 shape3d.py [slug ...]   (no args = every shape)
Output: ~/jarvis-projects/3d/<slug>/recipe.json (GLB built on demand into the cache)
"""

from __future__ import annotations

import itertools
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import ConvexHull

ROOT = Path.home() / "jarvis-projects" / "3d"
PHI = (1 + 5**0.5) / 2
TAU = 2 * math.pi


# ---------- mesh helpers ----------
def hull(pts):
    pts = np.unique(np.round(np.asarray(pts, float), 9), axis=0)
    h = ConvexHull(pts)
    f = h.simplices.copy()
    c = pts.mean(0)
    for k, (a, b, d) in enumerate(f):  # outward winding
        if np.dot(np.cross(pts[b] - pts[a], pts[d] - pts[a]), pts[a] - c) < 0:
            f[k] = (a, d, b)
    return pts, f


def grid(fn, nu, nv, closed_u=False, closed_v=False):
    us = np.linspace(0, 1, nu, endpoint=not closed_u)
    vs = np.linspace(0, 1, nv, endpoint=not closed_v)
    pts = np.array([fn(u, v) for u in us for v in vs], float)
    fc = []
    ru = nu if closed_u else nu - 1
    rv = nv if closed_v else nv - 1
    for i in range(ru):
        for j in range(rv):
            a = i * nv + j
            b = ((i + 1) % nu) * nv + j
            c = ((i + 1) % nu) * nv + (j + 1) % nv
            d = i * nv + (j + 1) % nv
            fc += [(a, b, c), (a, c, d)]
    return pts, np.array(fc)


def tube(path, r=0.05, seg=10):
    path = np.asarray(path, float)
    n = len(path)
    t = np.gradient(path, axis=0)
    t /= np.linalg.norm(t, axis=1, keepdims=True) + 1e-12
    ref = np.array([0, 0, 1.0])
    pts, fc = [], []
    for i in range(n):
        a = np.cross(t[i], ref)
        if np.linalg.norm(a) < 1e-6:
            a = np.cross(t[i], [0, 1.0, 0])
        a /= np.linalg.norm(a)
        b = np.cross(t[i], a)
        for k in range(seg):
            th = TAU * k / seg
            pts.append(path[i] + r * (math.cos(th) * a + math.sin(th) * b))
    for i in range(n - 1):
        for k in range(seg):
            a_ = i * seg + k
            b_ = i * seg + (k + 1) % seg
            fc += [(a_, b_, a_ + seg), (b_, b_ + seg, a_ + seg)]
    return np.array(pts), np.array(fc)


def merge(*parts):
    pts, fc, off = [], [], 0
    for p, f in parts:
        pts.append(p)
        fc.append(np.asarray(f) + off)
        off += len(p)
    return np.vstack(pts), np.vstack(fc)


def poly(verts_fn):
    return lambda: hull(verts_fn())


def perms(base, even=False, signs=True):
    out = set()
    for pm in itertools.permutations(range(3)):
        if even and (
            sum(1 for i in range(3) for j in range(i + 1, 3) if pm[i] > pm[j]) % 2
        ):
            continue
        for sg in itertools.product((-1, 1) if signs else (1,), repeat=3):
            out.add(tuple(round(base[pm[k]] * sg[k], 9) + 0.0 for k in range(3)))
    return list(out)


# ---------- shapes ----------
def cube():
    return hull(list(itertools.product((-1, 1), repeat=3)))


def tetra():
    return hull([(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)])


def octa():
    return hull(
        [
            p
            for i in range(3)
            for s in (-1, 1)
            for p in [tuple(s if k == i else 0 for k in range(3))]
        ]
    )


def dodeca():
    return hull(
        list(itertools.product((-1, 1), repeat=3))
        + [
            p
            for s in itertools.product((-1, 1), repeat=2)
            for p in [
                (0, s[0] / PHI, s[1] * PHI),
                (s[0] / PHI, s[1] * PHI, 0),
                (s[1] * PHI, 0, s[0] / PHI),
            ]
        ]
    )


def icosa():
    return hull(
        [
            p
            for s in itertools.product((-1, 1), repeat=2)
            for p in [
                (0, s[0], s[1] * PHI),
                (s[0], s[1] * PHI, 0),
                (s[1] * PHI, 0, s[0]),
            ]
        ]
    )


def trunc_icosa():
    return hull(
        perms((0, 1, 3 * PHI), True)
        + perms((1, 2 + PHI, 2 * PHI), True)
        + perms((PHI, 2, 2 * PHI + 1), True)
    )


def cuboct():
    return hull(perms((1, 1, 0)))


def rhombicubocta():
    return hull(perms((1, 1, 1 + 2**0.5)))


def stella():
    return merge(tetra(), hull([(-1, -1, -1), (-1, 1, 1), (1, -1, 1), (1, 1, -1)]))


def rhombic_dodeca():
    return hull(
        list(itertools.product((-1, 1), repeat=3))
        + [
            p
            for i in range(3)
            for s in (-2, 2)
            for p in [tuple(s if k == i else 0 for k in range(3))]
        ]
    )


def great_stellated_dodeca():
    p, _ = icosa()
    p = np.asarray(p)
    d = dodeca()[0]
    return merge(hull(p * 1.0), hull(d * 0.6))


def sphere(n=48):
    return grid(
        lambda u, v: (
            math.sin(math.pi * v) * math.cos(TAU * u),
            math.sin(math.pi * v) * math.sin(TAU * u),
            math.cos(math.pi * v),
        ),
        n,
        n // 2 + 1,
        closed_u=True,
    )


def icosphere():
    p, f = icosa()
    p = [tuple(np.array(x) / np.linalg.norm(x)) for x in p]
    f = [tuple(x) for x in f]
    for _ in range(3):
        mid, nf = {}, []

        def m(a, b):
            k = (min(a, b), max(a, b))
            if k not in mid:
                q = (np.array(p[a]) + np.array(p[b])) / 2
                p.append(tuple(q / np.linalg.norm(q)))
                mid[k] = len(p) - 1
            return mid[k]

        for a, b, c in f:
            ab, bc, ca = m(a, b), m(b, c), m(c, a)
            nf += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        f = nf
    return np.array(p), np.array(f)


def cylinder():
    return merge(
        grid(
            lambda u, v: (math.cos(TAU * u), math.sin(TAU * u), 2 * v - 1),
            48,
            2,
            closed_u=True,
        ),
        hull(
            [
                (math.cos(TAU * i / 48), math.sin(TAU * i / 48), s)
                for i in range(48)
                for s in (-1, 1)
            ]
        ),
    )


def cone():
    return hull(
        [(math.cos(TAU * i / 48), math.sin(TAU * i / 48), -1) for i in range(48)]
        + [(0, 0, 1)]
    )


def torus():
    return grid(
        lambda u, v: (
            (1 + 0.35 * math.cos(TAU * v)) * math.cos(TAU * u),
            (1 + 0.35 * math.cos(TAU * v)) * math.sin(TAU * u),
            0.35 * math.sin(TAU * v),
        ),
        64,
        24,
        True,
        True,
    )


def torus_knot(p=5, q=3, n=400):
    path = [
        (
            (2 + math.cos(q * t)) * math.cos(p * t),
            (2 + math.cos(q * t)) * math.sin(p * t),
            math.sin(q * t),
        )
        for t in np.linspace(0, TAU, n)
    ]
    return tube(path, 0.14, 12)


def trefoil():
    path = [
        (
            math.sin(t) + 2 * math.sin(2 * t),
            math.cos(t) - 2 * math.cos(2 * t),
            -math.sin(3 * t),
        )
        for t in np.linspace(0, TAU, 300)
    ]
    return tube(np.array(path) * 0.5, 0.12, 12)


def mobius():
    def f(u, v):
        a, w = TAU * u, (v - 0.5) * 0.8
        return (
            (1 + w * math.cos(a / 2)) * math.cos(a),
            (1 + w * math.cos(a / 2)) * math.sin(a),
            w * math.sin(a / 2),
        )

    return grid(f, 120, 12, closed_u=True)


def klein():
    def f(u, v):
        a, b = TAU * u, TAU * v
        r = 4 * (1 - math.cos(a) / 2)
        if a < math.pi:
            x = 6 * math.cos(a) * (1 + math.sin(a)) + r * math.cos(a) * math.cos(b)
            y = 16 * math.sin(a) + r * math.sin(a) * math.cos(b)
        else:
            x = 6 * math.cos(a) * (1 + math.sin(a)) + r * math.cos(b + math.pi)
            y = 16 * math.sin(a)
        return (x / 10, y / 10, r * math.sin(b) / 10)

    return grid(f, 100, 40, True, True)


def hyperboloid():
    return grid(
        lambda u, v: (
            math.sqrt(1 + (2 * v - 1) ** 2 * 2) * math.cos(TAU * u),
            math.sqrt(1 + (2 * v - 1) ** 2 * 2) * math.sin(TAU * u),
            (2 * v - 1) * 1.4,
        ),
        64,
        30,
        closed_u=True,
    )


def paraboloid():
    return grid(
        lambda u, v: (
            v * 1.4 * math.cos(TAU * u),
            v * 1.4 * math.sin(TAU * u),
            v * v * 1.4 * 1.4 * 0.7 - 0.7,
        ),
        64,
        24,
        closed_u=True,
    )


def saddle():
    return grid(
        lambda u, v: (2 * u - 1, 2 * v - 1, (2 * u - 1) ** 2 - (2 * v - 1) ** 2), 40, 40
    )


def helicoid():
    return grid(
        lambda u, v: (
            (2 * v - 1) * math.cos(4 * math.pi * u),
            (2 * v - 1) * math.sin(4 * math.pi * u),
            (u - 0.5) * 2.5,
        ),
        120,
        14,
    )


def catenoid():
    return grid(
        lambda u, v: (
            math.cosh(2 * v - 1) * math.cos(TAU * u),
            math.cosh(2 * v - 1) * math.sin(TAU * u),
            (2 * v - 1),
        ),
        64,
        30,
        closed_u=True,
    )


def sph_harm(l=3, m=2):
    from scipy.special import sph_harm_y

    def f(u, v):
        th, ph = math.pi * v, TAU * u
        r = abs(sph_harm_y(l, m, th, ph).real) ** 1.0
        return (
            r * math.sin(th) * math.cos(ph) * 2.2,
            r * math.sin(th) * math.sin(ph) * 2.2,
            r * math.cos(th) * 2.2,
        )

    return grid(f, 80, 60, closed_u=True)


def fib_sphere(n=600):
    pts = []
    for i in range(n):
        z = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(1 - z * z)
        a = TAU * i / PHI
        pts.append((r * math.cos(a), r * math.sin(a), z))
    pts = np.array(pts)
    sp = [hull([p * 0 + p])[0] if False else p for p in pts]
    # tiny octahedra as dots
    parts = []
    for p in sp:
        o, f = octa()
        parts.append((o * 0.035 + p, f))
    return merge(*parts)


def nested_platonic():
    sc = lambda a, k: (np.asarray(a[0]) * k, a[1])
    return merge(
        sc(octa(), 0.55),
        sc(icosa(), 0.8),
        sc(dodeca(), 1.1),
        sc(tetra(), 1.6),
        sc(cube(), 2.0),
    )


def lorenz():
    x, y, z = 1.0, 1.0, 1.0
    out = []
    for _ in range(6000):
        dx, dy, dz = 10 * (y - x), x * (28 - z) - y, x * y - 8 / 3 * z
        x, y, z = x + dx * 0.005, y + dy * 0.005, z + dz * 0.005
        out.append((x, y, z - 25))
    out = np.array(out) / 18
    return tube(out[::6], 0.02, 6)


def hopf(n=14):
    parts = []
    for k in range(n):
        th = math.pi * (k + 0.5) / n
        for j in range(3):
            ph = TAU * j / 3
            path = []
            for t in np.linspace(0, TAU, 90):
                z1 = math.cos(th / 2) * complex(math.cos(t), math.sin(t))
                z2 = math.sin(th / 2) * complex(math.cos(t + ph), math.sin(t + ph))
                w = 1 - z2.real
                path.append(
                    (
                        z1.real / (1.0001 - z2.imag + 0.0),
                        z1.imag / (1.0001 - z2.imag),
                        z2.real / (1.0001 - z2.imag),
                    )
                )
            parts.append(tube(np.clip(np.array(path), -4, 4) * 0.5, 0.02, 5))
    return merge(*parts)


def riemann():
    def sheet(sign):
        def f(u, v):
            w = complex(
                (0.15 + 1.3 * v) * math.cos(TAU * u),
                (0.15 + 1.3 * v) * math.sin(TAU * u),
            )
            return (w.real, w.imag, sign * (w**0.5).imag)

        return f

    return merge(
        grid(sheet(1), 90, 14, closed_u=True), grid(sheet(-1), 90, 14, closed_u=True)
    )


def gyroid(n=44):
    from skimage import measure

    ax = np.linspace(0, TAU, n)
    X, Y, Z = np.meshgrid(ax, ax, ax, indexing="ij")
    vol = np.sin(X) * np.cos(Y) + np.sin(Y) * np.cos(Z) + np.sin(Z) * np.cos(X)
    v, f, *_ = measure.marching_cubes(vol, 0)
    return (v - n / 2) / (n / 2) * 1.5, f[:, ::-1]


def schwarz_p(n=44):
    from skimage import measure

    ax = np.linspace(0, TAU, n)
    X, Y, Z = np.meshgrid(ax, ax, ax, indexing="ij")
    v, f, *_ = measure.marching_cubes(np.cos(X) + np.cos(Y) + np.cos(Z), 0)
    return (v - n / 2) / (n / 2) * 1.5, f[:, ::-1]


def schwarz_d(n=44):
    from skimage import measure

    ax = np.linspace(0, TAU, n)
    X, Y, Z = np.meshgrid(ax, ax, ax, indexing="ij")
    vol = (
        np.sin(X) * np.sin(Y) * np.sin(Z)
        + np.sin(X) * np.cos(Y) * np.cos(Z)
        + np.cos(X) * np.sin(Y) * np.cos(Z)
        + np.cos(X) * np.cos(Y) * np.sin(Z)
    )
    v, f, *_ = measure.marching_cubes(vol, 0)
    return (v - n / 2) / (n / 2) * 1.5, f[:, ::-1]


def menger(level=2):
    cells = [(0, 0, 0)]
    for _ in range(level):
        nxt = []
        for x, y, z in cells:
            for dx, dy, dz in itertools.product(range(3), repeat=3):
                if sum(1 for d in (dx, dy, dz) if d == 1) < 2:
                    nxt.append((x * 3 + dx, y * 3 + dy, z * 3 + dz))
        cells = nxt
    s = 2.0 / 3**level
    cp, cf = cube()
    parts = [
        (
            cp * 0.5 * s
            + np.array([(x + 0.5) * s - 1, (y + 0.5) * s - 1, (z + 0.5) * s - 1]),
            cf,
        )
        for x, y, z in cells
    ]
    return merge(*parts)


def sierpinski(level=4):
    tris = [np.array([(1, 1, 1), (1, -1, -1), (-1, 1, -1), (-1, -1, 1)], float)]
    for _ in range(level):
        nt = []
        for t in tris:
            for v in t:
                nt.append((t + v) / 2)
        tris = nt
    tp, tf = tetra()
    sc = 0.5**level
    return merge(
        *[
            (
                tp * sc + (t[0] - np.array(tetra()[0][0]) * sc if False else t.mean(0)),
                tf,
            )
            for t in tris
        ]
    )


def penrose():
    bars = []
    corners = [
        np.array(p, float) for p in [(0, 1.2, 0), (-1.1, -0.7, 0), (1.1, -0.7, 0)]
    ]
    for i in range(3):
        a, b = corners[i], corners[(i + 1) % 3]
        bars.append(tube([a, (a + b) / 2, b], 0.16, 4))
    return merge(*bars)


def boy_like():
    # Steiner Roman surface (Boy's needs heavy mesh); parametrised sphere map
    def f(u, v):
        t, p = math.pi * v, TAU * u
        x, y, z = math.sin(t) * math.cos(p), math.sin(t) * math.sin(p), math.cos(t)
        return (y * z * 1.6, x * z * 1.6, x * y * 1.6)

    return grid(f, 80, 40, closed_u=True)


def bohr_atom():
    parts = [
        sphere(24)[0] * 0.22,
    ]
    parts = [(sphere(24)[0] * 0.22, sphere(24)[1])]
    for rx, ry in ((0, 0), (60, 0), (120, 0)):
        path = [
            (
                math.cos(t),
                math.sin(t) * math.cos(math.radians(rx)),
                math.sin(t) * math.sin(math.radians(rx)),
            )
            for t in np.linspace(0, TAU, 80)
        ]
        p, f = tube(np.array(path) * 1.2, 0.02, 6)
        parts.append((p, f))
    return merge(*parts)


def graphene(nx=8, ny=6):
    pts = []
    for i in range(nx):
        for j in range(ny):
            ox, oy = i * 1.5, j * math.sqrt(3) + (math.sqrt(3) / 2 if i % 2 else 0)
            pts.append((ox, oy))
    parts = [
        (
            sphere(10)[0] * 0.13 + np.array([x - nx * 0.75, y - ny * 0.85, 0]),
            sphere(10)[1],
        )
        for x, y in pts
    ]
    return merge(*parts)


def fcc():
    pts = [p for p in itertools.product((0, 1), repeat=3)] + [
        (0.5, 0.5, 0),
        (0.5, 0.5, 1),
        (0.5, 0, 0.5),
        (0.5, 1, 0.5),
        (0, 0.5, 0.5),
        (1, 0.5, 0.5),
    ]
    s = sphere(12)
    return merge(*[(s[0] * 0.12 + (np.array(p) - 0.5) * 2, s[1]) for p in pts])


def bcc():
    pts = list(itertools.product((0, 1), repeat=3)) + [(0.5, 0.5, 0.5)]
    s = sphere(12)
    return merge(*[(s[0] * 0.14 + (np.array(p) - 0.5) * 2, s[1]) for p in pts])


def diamond_lattice():
    base = [p for p in itertools.product((0, 1), repeat=3)] + [
        (0.5, 0.5, 0),
        (0.5, 0.5, 1),
        (0.5, 0, 0.5),
        (0.5, 1, 0.5),
        (0, 0.5, 0.5),
        (1, 0.5, 0.5),
        (0.25, 0.25, 0.25),
        (0.75, 0.75, 0.25),
        (0.75, 0.25, 0.75),
        (0.25, 0.75, 0.75),
    ]
    s = sphere(12)
    return merge(*[(s[0] * 0.1 + (np.array(p) - 0.5) * 2, s[1]) for p in base])


def buckyball():
    p, _ = trunc_icosa()
    s = sphere(10)
    p = np.asarray(p)
    p = p / np.linalg.norm(p, axis=1).max() * 1.4
    return merge(*[(s[0] * 0.1 + v, s[1]) for v in p])


def nanotube():
    parts = []
    s = sphere(8)
    for i in range(24):
        for j in range(14):
            a = TAU * (i + (j % 2) * 0.5) / 24
            parts.append(
                (
                    s[0] * 0.06
                    + np.array([math.cos(a) * 0.8, math.sin(a) * 0.8, (j - 7) * 0.2]),
                    s[1],
                )
            )
    return merge(*parts)


def water():
    s = sphere(16)
    h = math.radians(52.25)
    return merge(
        (s[0] * 0.45, s[1]),
        (s[0] * 0.28 + np.array([math.sin(h), math.cos(h), 0]) * 1.0, s[1]),
        (s[0] * 0.28 + np.array([-math.sin(h), math.cos(h), 0]) * 1.0, s[1]),
    )


def methane():
    s = sphere(16)
    t = np.array(tetra()[0], float)
    t = t[:4] / np.linalg.norm(t[:4], axis=1, keepdims=True)
    return merge(
        (s[0] * 0.4, s[1]),
        *[(s[0] * 0.22 + v * 1.1, s[1]) for v in t[:4]],
        *[tube([v * 0.05, v * 1.1], 0.04, 6) for v in t[:4]],
    )


def accretion():
    d = grid(
        lambda u, v: (
            (0.5 + 2 * v) * math.cos(TAU * u),
            (0.5 + 2 * v) * math.sin(TAU * u),
            0.02 * math.sin(6 * TAU * u),
        ),
        120,
        12,
        closed_u=True,
    )
    s = sphere(32)
    j = [
        (cone()[0] * np.array([0.12, 0.12, 1.1]) + np.array([0, 0, 1.2 * z]), cone()[1])
        for z in (1, -1)
    ]
    j[1] = (j[1][0] * np.array([1, 1, -1]), j[1][1][:, ::-1]) if False else j[1]
    return merge(d, (s[0] * 0.45, s[1]), *j)


def saturn():
    s = sphere(40)
    rings = [
        grid(
            lambda u, v, a=a: (
                (a + 0.35 * v) * math.cos(TAU * u),
                (a + 0.35 * v) * math.sin(TAU * u),
                0,
            ),
            80,
            3,
            closed_u=True,
        )
        for a in (1.4, 1.9, 2.3)
    ]
    return merge((s[0], s[1]), *rings)


def solar_system():
    s = sphere(16)
    parts = [(s[0] * 0.5, s[1])]
    for i, (r, sz) in enumerate(
        [
            (0.9, 0.06),
            (1.2, 0.1),
            (1.6, 0.1),
            (2.0, 0.08),
            (2.8, 0.25),
            (3.5, 0.22),
            (4.1, 0.15),
            (4.6, 0.15),
        ]
    ):
        a = i * 1.3
        parts.append(
            (s[0] * sz + np.array([r * math.cos(a), r * math.sin(a), 0]), s[1])
        )
        parts.append(
            tube(
                [
                    (r * math.cos(t), r * math.sin(t), 0)
                    for t in np.linspace(0, TAU, 80)
                ],
                0.008,
                4,
            )
        )
    return merge(*parts)


def bloch():
    s = sphere(32)
    ax = [
        tube([(-1.3 * x, -1.3 * y, -1.3 * z), (1.3 * x, 1.3 * y, 1.3 * z)], 0.015, 5)
        for x, y, z in ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    ]
    eq = tube(
        [(math.cos(t), math.sin(t), 0) for t in np.linspace(0, TAU, 80)], 0.015, 5
    )
    st = tube([(0, 0, 0), (0.6, 0.3, 0.74)], 0.035, 6)
    return merge((s[0], s[1]), *ax, eq, st)


def hohmann():
    parts = [(sphere(16)[0] * 0.25, sphere(16)[1])]
    a, b = 1.0, 2.2
    parts.append(
        tube(
            [(a * math.cos(t), a * math.sin(t), 0) for t in np.linspace(0, TAU, 100)],
            0.01,
            4,
        )
    )
    parts.append(
        tube(
            [(b * math.cos(t), b * math.sin(t), 0) for t in np.linspace(0, TAU, 100)],
            0.01,
            4,
        )
    )
    sa = (a + b) / 2
    e = (b - a) / (a + b)
    parts.append(
        tube(
            [
                (sa * math.cos(t) - sa * e, sa * math.sqrt(1 - e * e) * math.sin(t), 0)
                for t in np.linspace(0, math.pi, 80)
            ],
            0.03,
            6,
        )
    )
    return merge(*parts)


def neural_net():
    s = sphere(10)
    layers, parts, pos = (4, 6, 6, 3), [], []
    for li, n in enumerate(layers):
        pos.append(
            [
                np.array(
                    [li * 1.0 - 1.5, (k - (n - 1) / 2) * 0.5, ((k * 7) % 3 - 1) * 0.25]
                )
                for k in range(n)
            ]
        )
    for L in pos:
        for p in L:
            parts.append((s[0] * 0.08 + p, s[1]))
    for a, b in zip(pos, pos[1:]):
        for p in a:
            for q in b:
                parts.append(tube([p, q], 0.008, 3))
    return merge(*parts)


def earth_field():
    parts = [(sphere(32)[0] * 0.6, sphere(32)[1])]
    for L in (1.4, 1.8, 2.3):
        for az in range(0, 360, 45):
            a = math.radians(az)
            pts = [
                (
                    L * math.cos(t) ** 2 * math.cos(t) * math.cos(a),
                    L * math.cos(t) ** 2 * math.cos(t) * math.sin(a),
                    L * math.cos(t) ** 2 * math.sin(t),
                )
                for t in np.linspace(-1.4, 1.4, 60)
            ]
            parts.append(tube(np.array(pts) * 1.0, 0.01, 4))
    return merge(*parts)


def spiral_inspiral():
    s = sphere(16)
    parts = []
    for sg in (1, -1):
        path = [
            (
                sg * (2.2 - 2 * t) * math.cos(8 * t * t),
                sg * (2.2 - 2 * t) * math.sin(8 * t * t),
                0,
            )
            for t in np.linspace(0, 0.98, 160)
        ]
        parts.append(tube(path, 0.02, 5))
        parts.append((s[0] * 0.18 + np.array(path[-1]), s[1]))
    return merge(*parts)


def voronoi_foam():
    from scipy.spatial import Voronoi

    rng = np.random.default_rng(3)
    pts = rng.uniform(-1, 1, (40, 3))
    vor = Voronoi(pts)
    parts = []
    for rv in vor.ridge_vertices:
        if -1 in rv:
            continue
        v = vor.vertices[rv]
        if np.abs(v).max() > 1.4:
            continue
        for a, b in zip(rv, rv[1:] + rv[:1]):
            parts.append(tube([vor.vertices[a], vor.vertices[b]], 0.012, 4))
    return merge(*parts)


def costa_like():
    return gyroid(36)


def mandelbulb(n=64):
    from skimage import measure

    ax = np.linspace(-1.25, 1.25, n)
    X, Y, Z = np.meshgrid(ax, ax, ax, indexing="ij")
    w = np.zeros_like(X)
    cx, cy, cz = X.copy(), Y.copy(), Z.copy()
    zx, zy, zz = X.copy(), Y.copy(), Z.copy()
    alive = np.ones_like(X, bool)
    for _ in range(8):
        r = np.sqrt(zx**2 + zy**2 + zz**2) + 1e-9
        th, ph = np.arccos(np.clip(zz / r, -1, 1)) * 8, np.arctan2(zy, zx) * 8
        rn = r**8
        nx, ny, nz = (
            rn * np.sin(th) * np.cos(ph) + cx,
            rn * np.sin(th) * np.sin(ph) + cy,
            rn * np.cos(th) + cz,
        )
        zx, zy, zz = (
            np.where(alive, nx, zx),
            np.where(alive, ny, zy),
            np.where(alive, nz, zz),
        )
        alive &= (zx**2 + zy**2 + zz**2) < 4
    vol = alive.astype(float)
    v, f, *_ = measure.marching_cubes(vol, 0.5)
    return (v - n / 2) / (n / 2) * 1.25, f


def utah_like():
    return merge(
        sphere(40),
        (lambda t: (t[0] * np.array([0.4, 0.4, 0.3]) + np.array([1.1, 0, 0.2]), t[1]))(
            sphere(20)
        ),
    )


SHAPES = {
    "cube": cube,
    "tetrahedron": tetra,
    "octahedron": octa,
    "dodecahedron": dodeca,
    "icosahedron": icosa,
    "sphere": sphere,
    "icosphere": icosphere,
    "cylinder": cylinder,
    "cone": cone,
    "torus": torus,
    "torus-knot-5-3": torus_knot,
    "trefoil-knot": trefoil,
    "mobius-strip": mobius,
    "klein-bottle": klein,
    "hyperboloid": hyperboloid,
    "paraboloid": paraboloid,
    "saddle": saddle,
    "helicoid": helicoid,
    "catenoid": catenoid,
    "truncated-icosahedron": trunc_icosa,
    "cuboctahedron": cuboct,
    "rhombicuboctahedron": rhombicubocta,
    "stella-octangula": stella,
    "rhombic-dodecahedron": rhombic_dodeca,
    "spherical-harmonic-f": sph_harm,
    "fibonacci-sphere": fib_sphere,
    "nested-platonic-solids": nested_platonic,
    "lorenz-attractor": lorenz,
    "hopf-fibration": hopf,
    "riemann-surface": riemann,
    "gyroid": gyroid,
    "schwarz-p-surface": schwarz_p,
    "schwarz-d-surface": schwarz_d,
    "menger-sponge": menger,
    "sierpinski-tetrahedron": sierpinski,
    "mandelbulb": mandelbulb,
    "steiner-roman-surface": boy_like,
    "voronoi-foam": voronoi_foam,
    "penrose-triangle": penrose,
    "bohr-atom": bohr_atom,
    "graphene": graphene,
    "fcc-lattice": fcc,
    "bcc-lattice": bcc,
    "diamond-lattice": diamond_lattice,
    "buckyball": buckyball,
    "carbon-nanotube": nanotube,
    "water-molecule": water,
    "methane-molecule": methane,
    "hohmann-transfer": hohmann,
    "black-hole-accretion": accretion,
    "saturn": saturn,
    "solar-system": solar_system,
    "bloch-sphere": bloch,
    "neural-network": neural_net,
    "earth-magnetic-field": earth_field,
    "gravitational-wave-inspiral": spiral_inspiral,
}


# ---------- GLB writer ----------
def write_glb(path: Path, v, f, colors=None):
    v = np.asarray(v, np.float32)
    v = v - (v.max(0) + v.min(0)) / 2
    v = v / (np.abs(v).max() or 1) * 1.0
    f = np.asarray(f, np.uint32).reshape(-1, 3)
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    cb = b""
    if colors is not None:
        cb = (
            np.asarray(colors, np.float32)[f]
            .reshape(-1, 3)
            .astype(np.float32)
            .tobytes()
        )
    pos = tri.reshape(-1, 3).astype(np.float32)  # flat shading
    nor = np.repeat(n, 3, axis=0).astype(np.float32)
    # glTF is Y-up; shapes are Z-up
    pos = pos[:, [0, 2, 1]] * np.array([1, 1, -1], np.float32)
    nor = nor[:, [0, 2, 1]] * np.array([1, 1, -1], np.float32)
    idx = np.arange(len(pos), dtype=np.uint32)
    bp, bn, bi = pos.tobytes(), nor.tobytes(), idx.tobytes()
    blob = bp + bn + bi + cb
    name = path.stem
    js = {
        "asset": {"version": "2.0", "generator": "shape3d.py"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [
            {
                "name": name,
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 0,
                            "NORMAL": 1,
                            **({"COLOR_0": 3} if cb else {}),
                        },
                        "indices": 2,
                        "material": 0,
                    }
                ],
            }
        ],
        "materials": [
            {
                "name": "holo",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [0.0, 0.85, 1.0, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.6,
                },
                "emissiveFactor": [0.0, 0.25, 0.35],
            }
        ],
        "buffers": [{"byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(bp), "target": 34962},
            {
                "buffer": 0,
                "byteOffset": len(bp),
                "byteLength": len(bn),
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": len(bp) + len(bn),
                "byteLength": len(bi),
                "target": 34963,
            },
        ]
        + (
            [
                {
                    "buffer": 0,
                    "byteOffset": len(bp) + len(bn) + len(bi),
                    "byteLength": len(cb),
                    "target": 34962,
                }
            ]
            if cb
            else []
        ),
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(pos),
                "type": "VEC3",
                "min": pos.min(0).tolist(),
                "max": pos.max(0).tolist(),
            },
            {"bufferView": 1, "componentType": 5126, "count": len(nor), "type": "VEC3"},
            {
                "bufferView": 2,
                "componentType": 5125,
                "count": len(idx),
                "type": "SCALAR",
            },
        ]
        + (
            [
                {
                    "bufferView": 3,
                    "componentType": 5126,
                    "count": len(pos),
                    "type": "VEC3",
                }
            ]
            if cb
            else []
        ),
    }
    j = json.dumps(js, separators=(",", ":")).encode()
    j += b" " * (-len(j) % 4)
    blob += b"\0" * (-len(blob) % 4)
    out = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(j) + 8 + len(blob))
    out += (
        struct.pack("<II", len(j), 0x4E4F534A)
        + j
        + struct.pack("<II", len(blob), 0x004E4942)
        + blob
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def build_all(only=None):
    """Store recipes only (a few bytes each); GLBs are built on demand by recipes.materialize."""
    import recipes

    ok, bad = [], []
    for slug, fn in SHAPES.items():
        if only and slug not in only:
            continue
        try:
            recipes.save(slug, {"kind": "shape3d", "name": slug})
            ok.append(slug)
        except Exception as e:  # noqa: BLE001
            bad.append((slug, repr(e)))
    return ok, bad


if __name__ == "__main__":
    ok, bad = build_all(set(sys.argv[1:]) or None)
    print(f"built {len(ok)}: {' '.join(ok)}")
    for s, e in bad:
        print("FAIL", s, e)
