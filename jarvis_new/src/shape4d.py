"""4D polytopes for the hologram: vertices in R^4 + edges, saved as shape4d.json.

The HUD rotates the vertices in 4D every frame and projects them to 3D, so the
shape visibly passes through the fourth dimension. No Blender/Codex needed.
"""

from __future__ import annotations

import itertools
import json
import math
import re
from pathlib import Path

PHI = (1 + math.sqrt(5)) / 2


def _edges_by_min_dist(v: list[list[float]]) -> list[list[int]]:
    best = min(math.dist(a, b) for a, b in itertools.combinations(v, 2))
    tol = best * 1e-3 + 1e-9
    return [
        [i, j]
        for (i, a), (j, b) in itertools.combinations(enumerate(v), 2)
        if abs(math.dist(a, b) - best) < tol
    ]


def _perm_parity(p: tuple[int, ...]) -> int:
    inv = sum(1 for i in range(len(p)) for j in range(i + 1, len(p)) if p[i] > p[j])
    return inv % 2


def tesseract():
    v = [list(c) for c in itertools.product((-1.0, 1.0), repeat=4)]
    return v, _edges_by_min_dist(v)


def cell5():
    s = 1 / math.sqrt(5)
    v = [
        [1, 1, 1, -s],
        [1, -1, -1, -s],
        [-1, 1, -1, -s],
        [-1, -1, 1, -s],
        [0, 0, 0, math.sqrt(5) - s],
    ]
    v = [[float(x) for x in p] for p in v]
    return v, [[i, j] for i, j in itertools.combinations(range(5), 2)]


def cell16():
    v = []
    for i in range(4):
        for s in (-1.0, 1.0):
            p = [0.0] * 4
            p[i] = s
            v.append(p)
    return v, _edges_by_min_dist(v)


def cell24():
    v = set()
    for i, j in itertools.combinations(range(4), 2):
        for si, sj in itertools.product((-1.0, 1.0), repeat=2):
            p = [0.0] * 4
            p[i], p[j] = si, sj
            v.add(tuple(p))
    v = [list(p) for p in sorted(v)]
    return v, _edges_by_min_dist(v)


def cell600():
    v = {tuple(c) for c in itertools.product((-0.5, 0.5), repeat=4)}
    for i in range(4):
        for s in (-1.0, 1.0):
            p = [0.0] * 4
            p[i] = s
            v.add(tuple(p))
    base = (PHI / 2, 0.5, 1 / (2 * PHI), 0.0)
    for perm in itertools.permutations(range(4)):
        if _perm_parity(perm):
            continue
        for signs in itertools.product((-1, 1), repeat=3):
            vals = [
                base[perm.index(k)] for k in range(4)
            ]  # coordinate k gets base[perm^-1]
            sg = iter(signs)
            p = tuple(x * next(sg) if x else 0.0 for x in vals)
            v.add(tuple(round(x, 12) for x in p))
    v = [list(p) for p in sorted(v)]
    return v, _edges_by_min_dist(v)


def duoprism(p: int, q: int):
    p, q = max(3, min(int(p), 24)), max(3, min(int(q), 24))
    v, idx = [], {}
    for i in range(p):
        for j in range(q):
            a, b = 2 * math.pi * i / p, 2 * math.pi * j / q
            idx[i, j] = len(v)
            v.append([math.cos(a), math.sin(a), math.cos(b), math.sin(b)])
    e = []
    for i in range(p):
        for j in range(q):
            e.append([idx[i, j], idx[(i + 1) % p, j]])
            e.append([idx[i, j], idx[i, (j + 1) % q]])
    return v, e


def sphere3(rings: int = 5, n: int = 24):
    """Clifford-torus family on the 3-sphere: nested tori through the 4th dimension."""
    v, e = [], []
    for k in range(1, rings + 1):
        eta = (math.pi / 2) * k / (rings + 1)
        for m in range(n):
            xi = 2 * math.pi * m / n
            for L in range(0, n, 3):
                z = 2 * math.pi * L / n
                v.append(
                    [
                        math.cos(eta) * math.cos(xi),
                        math.cos(eta) * math.sin(xi),
                        math.sin(eta) * math.cos(z),
                        math.sin(eta) * math.sin(z),
                    ]
                )
    # connect along both circle directions
    per = n * len(range(0, n, 3))
    nl = len(range(0, n, 3))
    for k in range(rings):
        base = k * per
        for m in range(n):
            for L in range(nl):
                a = base + m * nl + L
                e.append([a, base + m * nl + (L + 1) % nl])
                e.append([a, base + ((m + 1) % n) * nl + L])
    return v, e


def cell120():
    """Dual of the 600-cell: one vertex per tetrahedral cell (600 vertices)."""
    v, e = cell600()
    adj = {i: set() for i in range(len(v))}
    for a, b in e:
        adj[a].add(b)
        adj[b].add(a)
    cells = set()
    for a, b in e:
        for c in adj[a] & adj[b]:
            for d in adj[a] & adj[b] & adj[c]:
                cells.add(tuple(sorted((a, b, c, d))))
    out = [[sum(v[i][k] for i in c) / 4 for k in range(4)] for c in sorted(cells)]
    return out, _edges_by_min_dist(out)


def _perm_signs(base, even_only=False):
    out = set()
    for perm in itertools.permutations(range(4)):
        if even_only and _perm_parity(perm):
            continue
        for signs in itertools.product((-1, 1), repeat=4):
            p = tuple(round(base[perm[k]] * signs[k], 9) + 0.0 for k in range(4))
            out.add(p)
    return [list(p) for p in sorted(out)]


def truncated_tesseract():
    r = 1 + math.sqrt(2)
    v = _perm_signs((1.0, r, r, r))
    return v, _edges_by_min_dist(v)


def runcinated_tesseract():
    r = 1 + math.sqrt(2)
    v = _perm_signs((1.0, 1.0, 1.0, r))
    return v, _edges_by_min_dist(v)


def rectified_5cell():
    v5, _ = cell5()
    v = [[(a + b) / 2 for a, b in zip(p, q)] for p, q in itertools.combinations(v5, 2)]
    return v, _edges_by_min_dist(v)


def snub24():
    """Snub 24-cell: even permutations of (0, +-1, +-phi, +-phi^2)/2 (96 vertices)."""
    v = _perm_signs((0.0, 1.0, PHI, PHI * PHI), even_only=True)
    return v, _edges_by_min_dist(v)


def simplex6():
    """Regular 5-simplex (6 vertices) projected into R^4 by dropping one axis."""
    n = 6
    base = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    c = [sum(b[k] for b in base) / n for k in range(n)]
    pts = [[b[k] - c[k] for k in range(n)] for b in base]
    v = [p[:4] for p in pts]
    return v, [[i, j] for i, j in itertools.combinations(range(n), 2)]


def spherinder(rings: int = 7, n: int = 20):
    """Sphere x line: latitude rings of a 2-sphere at each w level."""
    v, e = [], []
    ws = [-1.0 + 2.0 * k / (rings - 1) for k in range(rings)]
    for w in ws:
        for lat in range(1, 5):
            th = math.pi * lat / 5
            for m in range(n):
                ph = 2 * math.pi * m / n
                v.append(
                    [
                        math.sin(th) * math.cos(ph),
                        math.sin(th) * math.sin(ph),
                        math.cos(th),
                        w,
                    ]
                )
    per = 4 * n
    for k in range(rings):
        for lat in range(4):
            for m in range(n):
                a = k * per + lat * n + m
                e.append([a, k * per + lat * n + (m + 1) % n])
                if lat < 3:
                    e.append([a, a + n])
                if k < rings - 1:
                    e.append([a, a + per])
    return v, e


def cubinder(n: int = 24):
    """Cube x circle in 4D: a cube swept around a circle (square x circle duoprism-like)."""
    v, e = [], []
    for j in range(n):
        b = 2 * math.pi * j / n
        for sx, sy in itertools.product((-1.0, 1.0), repeat=2):
            v.append([sx, sy, math.cos(b), math.sin(b)])
    for j in range(n):
        for k in range(4):
            a = j * 4 + k
            e.append([a, ((j + 1) % n) * 4 + k])
        for a, b in ((0, 1), (1, 3), (3, 2), (2, 0)):
            e.append([j * 4 + a, j * 4 + b])
    return v, e


def tesseract_net():
    """Unfolded tesseract cross (8 cubes) laid in R^3 with w=0 edges, as a net."""
    cells = [
        (0, 0, 0),
        (1, 0, 0),
        (-1, 0, 0),
        (0, 1, 0),
        (0, -1, 0),
        (0, 0, 1),
        (0, 0, -1),
        (0, 2, 0),
    ]
    pts, e = {}, set()
    for c in cells:
        ids = []
        for d in itertools.product((0, 1), repeat=3):
            q = tuple(c[k] + d[k] for k in range(3))
            pts.setdefault(q, len(pts))
            ids.append((d, pts[q]))
        for (d1, i), (d2, j) in itertools.combinations(ids, 2):
            if sum(abs(a - b) for a, b in zip(d1, d2)) == 1:
                e.add((min(i, j), max(i, j)))
    v = [[0.0] * 4 for _ in pts]
    for q, i in pts.items():
        v[i] = [float(q[0]), float(q[1]), float(q[2]), 0.0]
    return v, [list(x) for x in sorted(e)]


NAMES = {
    "tesseract": tesseract,
    "hypercube": tesseract,
    "8-cell": tesseract,
    "8 cell": tesseract,
    "5-cell": cell5,
    "5 cell": cell5,
    "pentachoron": cell5,
    "simplex": cell5,
    "hypertetrahedron": cell5,
    "16-cell": cell16,
    "16 cell": cell16,
    "hexadecachoron": cell16,
    "hyperoctahedron": cell16,
    "24-cell": cell24,
    "24 cell": cell24,
    "icositetrachoron": cell24,
    "octaplex": cell24,
    "600-cell": cell600,
    "600 cell": cell600,
    "hexacosichoron": cell600,
    "tetraplex": cell600,
    "120-cell": cell120,
    "120 cell": cell120,
    "hecatonicosachoron": cell120,
    "dodecaplex": cell120,
    "truncated tesseract": truncated_tesseract,
    "runcinated tesseract": runcinated_tesseract,
    "rectified 5-cell": rectified_5cell,
    "snub 24-cell": snub24,
    "snub 24 cell": snub24,
    "5-simplex": simplex6,
    "6-simplex": simplex6,
    "simplex chain": simplex6,
    "spherinder": spherinder,
    "cubinder": cubinder,
    "tesseract net": tesseract_net,
    "unfolded tesseract": tesseract_net,
    "hypersphere": sphere3,
    "3-sphere": sphere3,
    "glome": sphere3,
    "clifford torus": sphere3,
}
SUPPORTED = "tesseract, 5-cell, 16-cell, 24-cell, 120-cell, 600-cell, truncated/runcinated tesseract, snub 24-cell, spherinder, cubinder, tesseract net, duoprisms (e.g. 5-8 duoprism), hypersphere"


def build(name: str):
    """(label, verts, edges) for a spoken 4D shape name, or None."""
    t = re.sub(
        r"\b(a|an|the|4d|four[- ]dimensional|manipulatable|animated|wireframe|shape|polytope|please)\b",
        " ",
        (name or "").lower(),
    )
    t = re.sub(r"\s+", " ", t).strip()
    m = re.search(r"(\d+)\s*[-x,/ ]\s*(\d+)\s*duoprism", t) or re.search(
        r"duoprism\s*(\d+)\s*[-x,/ ]\s*(\d+)", t
    )
    if m:
        v, e = duoprism(int(m.group(1)), int(m.group(2)))
        return f"{m.group(1)}-{m.group(2)} duoprism", v, e
    if "duocylinder" in t or "tiger" in t:
        v, e = duoprism(24, 24)
        return "duocylinder", v, e
    if "duoprism" in t:
        v, e = duoprism(12, 12)
        return "duoprism", v, e
    for key in sorted(NAMES, key=len, reverse=True):
        if key in t:
            v, e = NAMES[key]()
            return key, v, e
    return None


def normalise(v: list[list[float]]) -> list[list[float]]:
    r = max(math.sqrt(sum(x * x for x in p)) for p in v) or 1.0
    return [[round(x / r, 6) for x in p] for p in v]


def save(folder: Path, label: str, v, e) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / "shape4d.json"
    p.write_text(json.dumps({"name": label, "verts": normalise(v), "edges": e}))
    return p
