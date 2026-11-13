"""Fast coloured holograms (no Blender): molecules from SMILES/names, and free-form builds from primitives.

Both write a GLB with per-vertex colour (COLOR_0). The HUD hologram keeps its wireframe/scan look but takes
its hue from the vertex colours (frontend/components/hud/holo-stage.tsx, aCol).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np

import recipes
import shape3d as S

ROOT = Path.home() / "jarvis-projects" / "3d"
TAU = 2 * math.pi

# ---------------------------------------------------------------- elements
# symbol: (valence, covalent radius A, display colour RGB 0..1)  CPK-ish, brightened for the hologram
EL = {
    "H": (1, 0.31, (0.95, 0.97, 1.0)),
    "C": (4, 0.76, (0.55, 0.6, 0.65)),
    "N": (3, 0.71, (0.25, 0.45, 1.0)),
    "O": (2, 0.66, (1.0, 0.2, 0.2)),
    "F": (1, 0.57, (0.5, 1.0, 0.4)),
    "Cl": (1, 1.02, (0.2, 0.95, 0.3)),
    "Br": (1, 1.2, (0.75, 0.25, 0.1)),
    "I": (1, 1.39, (0.6, 0.15, 0.85)),
    "S": (2, 1.05, (1.0, 0.9, 0.2)),
    "P": (3, 1.07, (1.0, 0.55, 0.1)),
    "B": (3, 0.84, (1.0, 0.7, 0.55)),
    "Si": (4, 1.11, (0.85, 0.7, 0.5)),
    "Na": (1, 1.66, (0.65, 0.35, 1.0)),
    "K": (1, 2.03, (0.6, 0.25, 0.85)),
    "Li": (1, 1.28, (0.8, 0.5, 1.0)),
    "Mg": (2, 1.41, (0.3, 0.9, 0.2)),
    "Ca": (2, 1.76, (0.3, 0.85, 0.3)),
    "Fe": (3, 1.32, (0.9, 0.45, 0.15)),
    "Cu": (2, 1.32, (0.85, 0.5, 0.25)),
    "Zn": (2, 1.22, (0.5, 0.55, 0.7)),
    "Al": (3, 1.21, (0.75, 0.65, 0.65)),
    "He": (0, 0.28, (0.85, 1.0, 1.0)),
    "Ne": (0, 0.58, (0.7, 0.9, 1.0)),
    "Ar": (0, 1.06, (0.5, 0.85, 0.95)),
}
ORGANIC = ("B", "C", "N", "O", "P", "S", "F", "Cl", "Br", "I")

# common names / formulas -> SMILES
NAMES = {
    "water": "O",
    "h2o": "O",
    "hydrogen": "[H][H]",
    "h2": "[H][H]",
    "oxygen": "O=O",
    "o2": "O=O",
    "nitrogen": "N#N",
    "n2": "N#N",
    "carbon dioxide": "O=C=O",
    "co2": "O=C=O",
    "carbon monoxide": "[C-]#[O+]",
    "co": "[C-]#[O+]",
    "ammonia": "N",
    "nh3": "N",
    "methane": "C",
    "ch4": "C",
    "ethane": "CC",
    "ethene": "C=C",
    "ethylene": "C=C",
    "ethyne": "C#C",
    "acetylene": "C#C",
    "propane": "CCC",
    "butane": "CCCC",
    "octane": "CCCCCCCC",
    "methanol": "CO",
    "ethanol": "CCO",
    "alcohol": "CCO",
    "acetone": "CC(=O)C",
    "acetic acid": "CC(=O)O",
    "formaldehyde": "C=O",
    "formic acid": "C(=O)O",
    "benzene": "c1ccccc1",
    "toluene": "Cc1ccccc1",
    "phenol": "Oc1ccccc1",
    "hydrogen peroxide": "OO",
    "h2o2": "OO",
    "hydrogen sulfide": "S",
    "h2s": "S",
    "sulfur dioxide": "O=S=O",
    "so2": "O=S=O",
    "sulfuric acid": "OS(=O)(=O)O",
    "h2so4": "OS(=O)(=O)O",
    "nitric acid": "O[N+](=O)[O-]",
    "hno3": "O[N+](=O)[O-]",
    "hydrochloric acid": "Cl",
    "hcl": "Cl",
    "hydrogen fluoride": "F",
    "hf": "F",
    "chlorine": "ClCl",
    "cl2": "ClCl",
    "ozone": "[O-][O+]=O",
    "o3": "[O-][O+]=O",
    "phosphoric acid": "OP(=O)(O)O",
    "h3po4": "OP(=O)(O)O",
    "glucose": "OCC1OC(O)C(O)C(O)C1O",
    "sucrose": "OCC1OC(OC2(CO)OC(CO)C(O)C2O)C(O)C(O)C1O",
    "glycine": "NCC(=O)O",
    "alanine": "CC(N)C(=O)O",
    "urea": "NC(=O)N",
    "caffeine": "Cn1cnc2c1c(=O)n(C)c(=O)n2C",
    "aspirin": "CC(=O)Oc1ccccc1C(=O)O",
    "paracetamol": "CC(=O)Nc1ccc(O)cc1",
    "ibuprofen": "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
    "nicotine": "CN1CCCC1c1cccnc1",
    "dopamine": "NCCc1ccc(O)c(O)c1",
    "serotonin": "NCCc1c[nH]c2ccc(O)cc12",
    "adrenaline": "CNCC(O)c1ccc(O)c(O)c1",
    "cholesterol": "CC(C)CCCC(C)C1CCC2C1(CCC3C2CC=C4C3(CCC(C4)O)C)C",
    "vitamin c": "OCC(O)C1OC(=O)C(O)=C1O",
    "ethylene glycol": "OCCO",
    "glycerol": "OCC(O)CO",
    "acetaldehyde": "CC=O",
    "propene": "CC=C",
    "cyclohexane": "C1CCCCC1",
    "naphthalene": "c1ccc2ccccc2c1",
    "pyridine": "c1ccncc1",
    "tnt": "Cc1c(cc(cc1[N+](=O)[O-])[N+](=O)[O-])[N+](=O)[O-]",
    "chloroform": "ClC(Cl)Cl",
    "methylamine": "CN",
    "hydrogen cyanide": "C#N",
    "hcn": "C#N",
    "nitrous oxide": "N#[N+][O-]",
    "no2": "O=[N]=O",
    "nitrogen dioxide": "O=[N+][O-]",
    "silane": "[SiH4]",
    "borane": "B",
    "diborane": "[BH2]1[H][BH2][H]1",
    "sf6": "FS(F)(F)(F)(F)F",
    "sulfur hexafluoride": "FS(F)(F)(F)(F)F",
    "pcl5": "ClP(Cl)(Cl)(Cl)Cl",
    "xef4": "F[Xe](F)(F)F",
    "tetrachloromethane": "ClC(Cl)(Cl)Cl",
    "ccl4": "ClC(Cl)(Cl)Cl",
    "freon": "FC(F)(Cl)Cl",
    "dna base adenine": "Nc1ncnc2[nH]cnc12",
    "adenine": "Nc1ncnc2[nH]cnc12",
    "thymine": "Cc1c[nH]c(=O)[nH]c1=O",
    "guanine": "Nc1nc2[nH]cnc2c(=O)[nH]1",
    "cytosine": "Nc1cc[nH]c(=O)n1",
    "atp": "Nc1ncnc2c1ncn2C1OC(COP(=O)(O)OP(=O)(O)OP(=O)(O)O)C(O)C1O",
}


# ---------------------------------------------------------------- SMILES -> graph
def parse_smiles(smi: str):
    """Minimal SMILES: atoms, [bracket atoms], branches, ring digits, = # bonds, aromatic lowercase."""
    atoms, bonds = [], []  # atoms: dict(sym, aromatic, charge, h)
    stack, prev, order, rings = [], None, 1, {}
    i = 0
    while i < len(smi):
        c = smi[i]
        if c == "(":
            stack.append(prev)
        elif c == ")":
            prev = stack.pop()
        elif c in "=#:-/\\":
            order = {"=": 2, "#": 3, ":": 1.5}.get(c, 1)
        elif c == ".":
            prev = None
        elif c.isdigit() or c == "%":
            if c == "%":
                key, i = smi[i + 1 : i + 3], i + 2
            else:
                key = c
            if key in rings:
                j, o = rings.pop(key)
                oo = max(o, order) if order != 1 else o
                if oo == 1 and atoms[j]["arom"] and atoms[prev]["arom"]:
                    oo = 1.5
                bonds.append((j, prev, oo))
                order = 1
            else:
                rings[key] = (prev, order)
                order = 1
        else:
            if c == "[":
                j = smi.index("]", i)
                m = re.match(
                    r"(\d*)([A-Z][a-z]?|[a-z]{1,2})(@*)(H\d*)?([+-]+\d*)?",
                    smi[i + 1 : j],
                )
                sym, hs, ch = m.group(2), m.group(4), m.group(5)
                arom = sym[0].islower()
                sym = sym.capitalize()
                h = None if hs is None else (1 if hs == "H" else int(hs[1:]))
                charge = 0
                if ch:
                    n = int(ch[1:]) if len(ch) > 1 and ch[1:].isdigit() else len(ch)
                    charge = n if ch[0] == "+" else -n
                atom = dict(
                    sym=sym,
                    arom=arom,
                    charge=charge,
                    h=0 if h is None else h,
                    fixed=True,
                )
                i = j
            else:
                two = smi[i : i + 2]
                if two in ("Cl", "Br"):
                    sym, i = two, i + 1
                else:
                    sym = c
                arom = sym.islower()
                atom = dict(sym=sym.capitalize(), arom=arom, charge=0, h=0, fixed=False)
            atoms.append(atom)
            k = len(atoms) - 1
            if prev is not None:
                o = order
                if o == 1 and atoms[prev]["arom"] and atom["arom"]:
                    o = 1.5
                bonds.append((prev, k, o))
            prev, order = k, 1
        i += 1
    # implicit hydrogens
    used = [0.0] * len(atoms)
    for a, b, o in bonds:
        used[a] += o if o != 1.5 else 1.5
        used[b] += o if o != 1.5 else 1.5
    for k, a in enumerate(atoms):
        if a["fixed"]:
            continue
        val = EL.get(a["sym"], (0,))[0]
        u = used[k]
        a["h"] = (
            max(0, int(round(val - u)))
            if a["sym"] in ORGANIC or a["sym"] in ("Si", "Al")
            else 0
        )
    # add H atoms
    for k in range(len(atoms)):
        for _ in range(atoms[k]["h"]):
            atoms.append(dict(sym="H", arom=False, charge=0, h=0, fixed=True))
            bonds.append((k, len(atoms) - 1, 1))
    return atoms, bonds


# ---------------------------------------------------------------- 3D embedding
def _embed_once(atoms, bonds, iters=600, seed=7):
    n = len(atoms)
    rng = np.random.default_rng(seed)
    nb = [[] for _ in range(n)]
    for a, b, o in bonds:
        nb[a].append(b)
        nb[b].append(a)
    rad = np.array([EL.get(a["sym"], (0, 1.0))[1] for a in atoms])
    # bond targets
    pairs, tgt = [], []
    for a, b, o in bonds:
        L = (
            rad[a]
            + rad[b]
            - (0.08 if o == 2 else 0.15 if o == 3 else 0.04 if o == 1.5 else 0)
        )
        pairs.append((a, b))
        tgt.append(L)
    # angle (1-3) targets from steric number
    p13, t13 = [], []
    for c in range(n):
        ns = nb[c]
        if len(ns) < 2:
            continue
        val = EL.get(atoms[c]["sym"], (4,))[0]
        pi = sum(max(0, o - 1) if o != 1.5 else 0.5 for a, b, o in bonds if c in (a, b))
        ch = atoms[c]["charge"]
        lone = max(
            0,
            (
                (
                    {
                        "H": 1,
                        "C": 4,
                        "N": 5,
                        "O": 6,
                        "F": 7,
                        "Cl": 7,
                        "S": 6,
                        "P": 5,
                        "B": 3,
                    }.get(atoms[c]["sym"], 4)
                    - ch
                )
                - (len(ns) + pi)
            )
            // 2,
        )
        sn = len(ns) + lone
        deg = {2: 180.0, 3: 120.0, 4: 109.5, 5: 90.0, 6: 90.0}.get(sn, 109.5)
        if sn == 3 and lone == 1:
            deg = 117.0
        if sn == 4 and lone == 1:
            deg = 107.0
        if sn == 4 and lone == 2:
            deg = 104.5
        if sn >= 5:
            deg = 100.0
        if all(atoms[x]["arom"] for x in (c, *ns[:1])) and atoms[c]["arom"]:
            deg = 120.0
        for x in range(len(ns)):
            for y in range(x + 1, len(ns)):
                a, b = ns[x], ns[y]
                lab = math.dist((0, 0), (1, 0))
                la = next(t for (p, q), t in zip(pairs, tgt) if {p, q} == {c, a})
                lb = next(t for (p, q), t in zip(pairs, tgt) if {p, q} == {c, b})
                d = math.sqrt(
                    la * la + lb * lb - 2 * la * lb * math.cos(math.radians(deg))
                )
                p13.append((a, b))
                t13.append(d)
    X = rng.normal(0, 1.0, (n, 3))
    P = np.array(pairs).reshape(-1, 2)
    T = np.array(tgt)
    P3 = np.array(p13).reshape(-1, 2)
    T3 = np.array(t13)
    mask = np.ones((n, n))
    for a_, b_ in list(map(tuple, P)) + list(map(tuple, P3)):
        mask[a_, b_] = mask[b_, a_] = 0.0
    for it in range(iters):
        F = np.zeros_like(X)
        for P_, T_, k in ((P, T, 0.5), (P3, T3, 0.3)):
            if len(P_):
                d = X[P_[:, 1]] - X[P_[:, 0]]
                L = np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
                f = k * (L - T_[:, None]) * d / L
                np.add.at(F, P_[:, 0], f)
                np.add.at(F, P_[:, 1], -f)
        D = X[:, None] - X[None]
        R = np.linalg.norm(D, axis=2) + np.eye(n) + 1e-6
        rep = (mask / (R**3))[..., None] * D * 0.12
        rep[np.arange(n), np.arange(n)] = 0
        F += rep.sum(1)
        X += F * (0.5 * (1 - it / iters) + 0.05)
    for it in range(160):  # polish: stiffer bonds, no cooling
        F = np.zeros_like(X)
        for P_, T_, k in ((P, T, 1.0), (P3, T3, 0.35)):
            if len(P_):
                d = X[P_[:, 1]] - X[P_[:, 0]]
                L = np.linalg.norm(d, axis=1, keepdims=True) + 1e-9
                f = k * (L - T_[:, None]) * d / L
                np.add.at(F, P_[:, 0], f)
                np.add.at(F, P_[:, 1], -f)
        D = X[:, None] - X[None]
        R = np.linalg.norm(D, axis=2) + np.eye(n) + 1e-6
        rep = (mask / (R**3))[..., None] * D * 0.12
        rep[np.arange(n), np.arange(n)] = 0
        F += rep.sum(1)
        X += F * 0.3
    X -= X.mean(0)
    # flatten aromatic systems: project onto best-fit plane when every heavy atom is aromatic ring member
    if any(a["arom"] for a in atoms):
        idx = [i for i, a in enumerate(atoms) if a["arom"]]
        if (
            len(idx) >= 5
            and len(idx)
            == sum(1 for a in atoms if a["sym"] != "H" and (a["arom"] or True))
            and len(idx) == len([a for a in atoms if a["sym"] != "H"])
        ):
            u, s, vt = np.linalg.svd(X[idx] - X[idx].mean(0))
            nrm = vt[2]
            X = X - np.outer((X - X[idx].mean(0)) @ nrm, nrm) * 0.9
    return X


def _energy(atoms, bonds, X):
    rad = np.array([EL.get(a["sym"], (0, 1.0))[1] for a in atoms])
    e = sum(
        ((np.linalg.norm(X[i] - X[j]) - (rad[i] + rad[j])) / (rad[i] + rad[j])) ** 2
        for i, j, o in bonds
    )
    n = len(atoms)
    D = np.linalg.norm(X[:, None] - X[None], axis=2) + np.eye(n) * 9
    R = rad[:, None] + rad[None]
    bonded = np.zeros((n, n), bool)
    for i, j, o in bonds:
        bonded[i, j] = bonded[j, i] = True
    clash = np.clip(1.0 - D / R, 0, None) * (~bonded)
    return e + 5.0 * float((clash**2).sum())


def embed(atoms, bonds, iters=600, seed=7):
    """Best of a few relaxations (fewer for big molecules to stay within about a second)."""
    n = len(atoms)
    tries = 4 if n <= 30 else 3 if n <= 60 else 2 if n <= 90 else 1
    best, be = None, 1e18
    for k in range(tries):
        X = _embed_once(atoms, bonds, iters, seed + 11 * k)
        e = _energy(atoms, bonds, X)
        if e < be:
            best, be = X, e
    return best


# ---------------------------------------------------------------- meshing with colour
def cyl(p, q, r, col, seg=8):
    path = [p, q]
    pts, f = S.tube(np.array([p, (np.array(p) + q) / 2, q], float), r, seg)
    return pts, f


def _ring_atoms(n, bonds):
    adj = {i: [] for i in range(n)}
    for a, b, o in bonds:
        adj[a].append(b)
        adj[b].append(a)
    inring = set()
    for a, b, o in bonds:
        # edge is in a ring iff b still reachable from a without it
        seen, st = {a}, [a]
        while st:
            u = st.pop()
            for w in adj[u]:
                if (u, w) in ((a, b), (b, a)) or w in seen:
                    continue
                seen.add(w)
                st.append(w)
        if b in seen:
            inring.update((a, b))
    return inring


def atom_colors(atoms, bonds):
    """Multi-colour scheme: element hue, with carbon/oxygen/nitrogen split by chemical environment."""
    n = len(atoms)
    ring = _ring_atoms(n, bonds)
    nb = {i: [] for i in range(n)}
    for a, b, o in bonds:
        nb[a].append((b, o))
        nb[b].append((a, o))
    out = []
    for i, a in enumerate(atoms):
        sym = a["sym"]
        base = np.array(EL.get(sym, (0, 1, (0.8, 0.8, 0.8)))[2], float)
        ns = nb[i]
        syms = [atoms[j]["sym"] for j, _ in ns]
        if sym == "C":
            if a["arom"] or any(o == 1.5 for _, o in ns):
                col = (0.15, 0.9, 0.8)  # aromatic carbon: teal
            elif any(atoms[j]["sym"] == "O" and o == 2 for j, o in ns):
                col = (1.0, 0.72, 0.2)  # carbonyl carbon: amber
            elif any(o == 3 for _, o in ns):
                col = (0.75, 0.4, 1.0)  # triple bond: violet
            elif any(o == 2 for _, o in ns):
                col = (0.4, 1.0, 0.55)  # alkene: green
            elif any(s in ("N", "O", "S") for s in syms):
                col = (1.0, 0.55, 0.7)  # carbon bonded to a heteroatom: pink
            elif i in ring:
                col = (0.4, 0.6, 1.0)  # ring carbon: blue
            else:
                col = (0.62, 0.68, 0.76)  # chain carbon: slate
        elif sym == "O":
            col = (1.0, 0.45, 0.1) if any(o == 2 for _, o in ns) else (1.0, 0.2, 0.25)
            if "H" in syms:
                col = (1.0, 0.3, 0.5)  # hydroxyl: rose
        elif sym == "N":
            col = (0.35, 0.5, 1.0) if "H" not in syms else (0.5, 0.7, 1.0)
        elif sym == "H":
            col = (0.85, 0.95, 1.0)
        else:
            col = tuple(base)
        out.append(np.array(col, float))
    return out


def build_mesh(atoms, bonds, X, cols=None):
    parts = []
    big = len(atoms) > 40
    sph = S.sphere(9 if big else 14)
    cols = cols if cols is not None else atom_colors(atoms, bonds)
    for ai, (a, x) in enumerate(zip(atoms, X)):
        el = EL.get(a["sym"], (0, 1.0, (0.8, 0.8, 0.8)))
        r = (0.28 + 0.22 * el[1]) * (0.7 if a["sym"] == "H" else 1.0)
        col = cols[ai]
        parts.append((sph[0] * r + x, sph[1], np.tile(col, (len(sph[0]), 1))))
    for a, b, o in bonds:
        A, B = X[a], X[b]
        d = B - A
        L = np.linalg.norm(d) + 1e-9
        u = d / L
        ref = np.array([0, 0, 1.0]) if abs(u[2]) < 0.9 else np.array([1.0, 0, 0])
        w = np.cross(u, ref)
        w /= np.linalg.norm(w)
        k = {1: 1, 1.5: 1, 2: 2, 3: 3}.get(o, 1)
        offs = {1: [0], 2: [-0.11, 0.11], 3: [-0.16, 0, 0.16]}[k]
        ca, cb = cols[a], cols[b]
        for off in offs:
            O = w * off
            pts = [A + O + d * t for t in np.linspace(0, 1, 7)]
            p, f = S.tube(np.array(pts), 0.055 if k == 1 else 0.04, 5 if big else 6)
            t = np.repeat(np.linspace(0, 1, 7), 6)[:, None]
            parts.append((p, f, ca * (1 - t) + cb * t))
    pts, fc, cols, off = [], [], [], 0
    for p, f, c in parts:
        pts.append(p)
        fc.append(np.asarray(f) + off)
        cols.append(c)
        off += len(p)
    return np.vstack(pts), np.vstack(fc), np.vstack(cols)


def write_colored(path: Path, pts, faces, cols) -> Path:
    S.write_glb(path, pts, faces, np.clip(cols, 0.04, 1.0))
    return path


def special_graph(name: str):
    """Explicit-geometry molecules: (atoms, bonds, X, atom colours)."""
    if name == "c60":
        P = np.asarray(S.trunc_icosa()[0], float)
        P = P / np.linalg.norm(P, axis=1).max() * 3.5
        d = min(
            np.linalg.norm(P[i] - P[j])
            for i in range(len(P))
            for j in range(i + 1, len(P))
        )
        bonds = [
            (i, j, 1.5)
            for i in range(len(P))
            for j in range(i + 1, len(P))
            if abs(np.linalg.norm(P[i] - P[j]) - d) < d * 0.02
        ]
        atoms = [dict(sym="C", arom=True, charge=0, h=0, fixed=True) for _ in P]
        import colorsys

        cols = [
            np.array(
                colorsys.hsv_to_rgb(0.5 + 0.45 * (p[2] / 3.5 * 0.5 + 0.5), 0.75, 1.0)
            )
            for p in P
        ]
        return atoms, bonds, P, cols
    raise ValueError(name)


def render_molecule(smiles: str, out: Path) -> Path:
    if smiles.startswith("@"):
        atoms, bonds, X, cols = special_graph(smiles[1:])
        pts, fc, cl = build_mesh(atoms, bonds, X, cols)
        return write_colored(out, pts, fc, cl)
    atoms, bonds = parse_smiles(smiles)
    X = embed(atoms, bonds)
    pts, fc, cols = build_mesh(atoms, bonds, X)
    return write_colored(out, pts, fc, cols)


# ---------------------------------------------------------------- public: molecules
def _slug(prefix: str, name: str) -> str:
    return prefix + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40]


def formula_of(atoms) -> str:
    cnt = {}
    for a in atoms:
        cnt[a["sym"]] = cnt.get(a["sym"], 0) + 1
    order = sorted(cnt, key=lambda s: (s != "C", s != "H", s))
    if "C" not in cnt:
        order = sorted(cnt)
    return "".join(f"{s}{cnt[s] if cnt[s] > 1 else ''}" for s in order)


_LIB: list[tuple[str, str]] | None = None


def library() -> list[tuple[str, str]]:
    """Saved molecule library: (smiles, formula) rows from ~/jarvis-projects/molecules/library.tsv."""
    global _LIB
    if _LIB is None:
        try:
            _LIB = [
                tuple(l.split("\t"))
                for l in (ROOT.parent / "molecules" / "library.tsv")
                .read_text()
                .splitlines()
                if "\t" in l
            ]
        except OSError:
            _LIB = []
    return _LIB


_HARD: dict[str, tuple[str, str]] | None = None
ALIAS = {
    "buckyball": "buckminsterfullerene",
    "c60": "buckminsterfullerene",
    "fullerene": "buckminsterfullerene",
    "tetrahydrocannabinol": "thc",
    "cbd": "cannabidiol",
    "taxol paclitaxel": "taxol",
    "paclitaxel": "taxol",
    "vitamin d": "vitamin d3",
    "cholecalciferol": "vitamin d3",
    "tocopherol": "vitamin e",
    "folate": "folic acid",
    "pyridoxine": "vitamin b6",
    "viagra": "sildenafil",
    "lipitor": "atorvastatin",
    "valium": "diazepam",
    "prozac": "fluoxetine",
    "penicillin": "penicillin g",
    "bpa": "bisphenol a",
    "dioxin": "tcdd",
    "carotene": "beta-carotene",
    "ptfe": "perfluorohexane",
    "teflon": "perfluorohexane",
    "kevlar": "kevlar fragment",
    "nylon": "nylon-6,6 fragment",
    "polyethylene": "polyethylene fragment",
    "peptide": "tripeptide gly-ala-ser",
    "nad": "nad+",
    "estrogen": "estradiol",
}


def hard_library() -> dict[str, tuple[str, str]]:
    """Hard / large molecules: name -> (smiles, formula) from ~/jarvis-projects/molecules/hard.tsv."""
    global _HARD
    if _HARD is None:
        _HARD = {}
        try:
            for l in (ROOT.parent / "molecules" / "hard.tsv").read_text().splitlines():
                p = l.split("\t")
                if len(p) == 3:
                    _HARD[p[0]] = (p[1], p[2])
        except OSError:
            pass
    return _HARD


def _from_library(key: str, q: str):
    """'random molecule', 'molecule 123', or a formula like C3H6O -> a library SMILES (or None)."""
    lib = library()
    if not lib:
        return None
    if re.fullmatch(r"(random|any|some)( molecule| compound)?", key):
        import random

        return random.choice(lib)[0]
    m = re.fullmatch(r"(?:number |no |# ?|id )?(\d{1,6})", key)
    if m and 0 < int(m.group(1)) <= len(lib):
        return lib[int(m.group(1)) - 1][0]
    qq = q.replace(" ", "")
    if re.fullmatch(r"(?:[A-Z][a-z]?\d*)+", qq):
        hits = [s for s, f in lib if f == qq]
        if hits:
            import random

            return random.choice(hits)
    return None


def make_molecule(query: str) -> dict:
    """query = common name, formula, or a SMILES string. Returns {slug, label, formula, atoms, bonds}."""
    q = (query or "").strip()
    key = re.sub(
        r"\b(the|a|an|molecule|compound|of|build|make|3d|model)\b", " ", q.lower()
    )
    key = re.sub(r"\s+", " ", key).strip()
    key = re.sub(r"[\s_]+", " ", key)
    hk = ALIAS.get(key, key)
    if hk not in hard_library():
        hk = ALIAS.get(key.replace(" ", "-"), key.replace(" ", "-"))
    smi = (
        hard_library().get(hk, (None,))[0]
        or NAMES.get(key)
        or NAMES.get(key.replace(" ", ""))
    )
    if smi is None:
        smi = _from_library(key, q)
    if smi is None:
        if re.fullmatch(r"[A-Za-z0-9@+\-\[\]\(\)=#$/\\.%:]+", q):
            smi = q
    if smi is None:
        raise ValueError(f"unknown molecule: {query!r}; pass a SMILES string")
    if smi.startswith("@"):
        slug = _slug("mol-", key or q)
        recipes.save(slug, {"kind": "molecule", "smiles": smi, "label": key or q})
        at, bo, _, _ = special_graph(smi[1:])
        return {
            "slug": slug,
            "label": key or q,
            "formula": formula_of(at),
            "atoms": len(at),
            "bonds": len(bo),
        }
    if re.search(r"[a-z]{4,}", smi):
        raise ValueError(f"unknown molecule: {query!r}; pass a SMILES string")
    atoms, bonds = parse_smiles(smi)
    if any(a["sym"] not in EL for a in atoms):
        raise ValueError(f"unknown molecule: {query!r}; pass a SMILES string")
    slug = _slug("mol-", key or q)
    recipes.save(slug, {"kind": "molecule", "smiles": smi, "label": key or q})
    return {
        "slug": slug,
        "label": key or q,
        "formula": formula_of(atoms),
        "atoms": len(atoms),
        "bonds": len(bonds),
    }


# ---------------------------------------------------------------- public: free-form coloured build
PRIMS = {
    "box": lambda: S.cube(),
    "cube": lambda: S.cube(),
    "sphere": lambda: S.sphere(20),
    "cylinder": lambda: S.cylinder(),
    "cone": lambda: S.cone(),
    "torus": lambda: S.torus(),
    "pyramid": lambda: S.hull(
        [(1, 1, -1), (1, -1, -1), (-1, 1, -1), (-1, -1, -1), (0, 0, 1)]
    ),
    "tetra": lambda: S.tetra(),
    "octa": lambda: S.octa(),
    "icosa": lambda: S.icosa(),
    "dodeca": lambda: S.dodeca(),
    "capsule": lambda: S.merge(
        S.cylinder(),
        (S.sphere(14)[0] * 1 + np.array([0, 0, 1]), S.sphere(14)[1]),
        (S.sphere(14)[0] * 1 + np.array([0, 0, -1]), S.sphere(14)[1]),
    ),
    "disc": lambda: S.hull(
        [
            (math.cos(TAU * i / 32), math.sin(TAU * i / 32), s * 0.08)
            for i in range(32)
            for s in (-1, 1)
        ]
    ),
}
COLORS = {
    "red": (1, 0.15, 0.15),
    "green": (0.2, 1, 0.35),
    "blue": (0.2, 0.45, 1),
    "cyan": (0.2, 0.95, 1),
    "yellow": (1, 0.9, 0.2),
    "orange": (1, 0.55, 0.1),
    "purple": (0.65, 0.3, 1),
    "pink": (1, 0.4, 0.75),
    "white": (0.95, 0.97, 1),
    "grey": (0.55, 0.6, 0.65),
    "gray": (0.55, 0.6, 0.65),
    "black": (0.15, 0.15, 0.2),
    "brown": (0.6, 0.35, 0.15),
    "gold": (1, 0.8, 0.25),
    "silver": (0.8, 0.85, 0.9),
    "teal": (0.1, 0.8, 0.7),
    "magenta": (1, 0.2, 0.8),
    "lime": (0.6, 1, 0.1),
    "navy": (0.1, 0.2, 0.6),
}


def _rgb(c) -> np.ndarray:
    if isinstance(c, str):
        c = c.strip().lower()
        if c in COLORS:
            return np.array(COLORS[c], float)
        m = re.fullmatch(r"#?([0-9a-f]{6})", c)
        if m:
            h = m.group(1)
            return np.array([int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)])
        return np.array(COLORS["cyan"])
    return np.clip(np.array(c, float), 0, 1)


def render_build(parts: list[dict], out: Path) -> Path:
    """parts: [{shape, pos:[x,y,z], size:[sx,sy,sz]|float, rot:[rx,ry,rz deg], color}] (Z up)."""
    if not parts:
        raise ValueError("no parts")
    items = []
    for p in parts[:400]:
        shp = str(p.get("shape", "box")).lower()
        if shp not in PRIMS:
            shp = "box"
        pts, f = PRIMS[shp]()
        pts = np.asarray(pts, float)
        pts = pts / (np.abs(pts).max() or 1.0)
        sz = p.get("size", 1.0)
        sz = (
            np.array(
                [sz] * 3
                if isinstance(sz, (int, float))
                else (list(sz) + [1, 1, 1])[:3],
                float,
            )
            * 0.5
        )
        pts = pts * sz
        rx, ry, rz = [
            math.radians(a) for a in (list(p.get("rot", [0, 0, 0])) + [0, 0, 0])[:3]
        ]
        Rx = np.array(
            [
                [1, 0, 0],
                [0, math.cos(rx), -math.sin(rx)],
                [0, math.sin(rx), math.cos(rx)],
            ]
        )
        Ry = np.array(
            [
                [math.cos(ry), 0, math.sin(ry)],
                [0, 1, 0],
                [-math.sin(ry), 0, math.cos(ry)],
            ]
        )
        Rz = np.array(
            [
                [math.cos(rz), -math.sin(rz), 0],
                [math.sin(rz), math.cos(rz), 0],
                [0, 0, 1],
            ]
        )
        pts = pts @ (Rz @ Ry @ Rx).T + np.array(
            (list(p.get("pos", [0, 0, 0])) + [0, 0, 0])[:3], float
        )
        items.append((pts, f, np.tile(_rgb(p.get("color", "cyan")), (len(pts), 1))))
    pts, fc, cols, off = [], [], [], 0
    for p, f, c in items:
        pts.append(p)
        fc.append(np.asarray(f) + off)
        cols.append(c)
        off += len(p)
    return write_colored(out, np.vstack(pts), np.vstack(fc), np.vstack(cols))


def make_build(name: str, parts: list[dict]) -> dict:
    if not parts:
        raise ValueError("no parts")
    slug = _slug("col-", name)
    recipes.save(slug, {"kind": "build", "name": name, "parts": parts[:400]})
    return {"slug": slug, "label": name, "parts": len(parts)}
