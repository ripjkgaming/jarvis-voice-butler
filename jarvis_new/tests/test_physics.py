"""Physics engine: analytic checks (range, orbit energy, momentum, pendulum period, rods)."""

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import physics as P  # noqa: E402


def test_projectile_range_matches_analytic():
    r = P.simulate({"preset": "projectile", "speed": 30, "angle": 45, "height": 0.0})[
        "summary"
    ]
    ideal = 30**2 * math.sin(math.radians(90)) / 9.81
    assert abs(r["range_m"] - ideal) / ideal < 0.02
    assert (
        abs(r["max_height_m"] - (30 * math.sin(math.radians(45))) ** 2 / (2 * 9.81))
        < 0.2
    )


def test_orbit_conserves_energy():
    r = P.simulate({"preset": "orbit", "radius": 1.0, "duration": 2.0})["summary"]
    assert r["energy_drift"] < 1e-4
    assert (
        abs(r["min_distance_au"] - 1.0) < 0.01
        and abs(r["max_distance_au"] - 1.0) < 0.01
    )


def test_elastic_collision_conserves_momentum():
    w = P.World(g=np.zeros(3), ground=None, collide=True)
    w.add((-1, 0, 0), (2, 0, 0), mass=1, radius=0.2, restitution=1.0)
    w.add((1, 0, 0), (0, 0, 0), mass=1, radius=0.2, restitution=1.0)
    p0 = w.momentum().copy()
    for _ in range(2000):
        w.step(1e-3)
    assert np.allclose(w.momentum(), p0, atol=1e-9)
    assert (
        abs(w.vel[1, 0] - 2.0) < 1e-6 and abs(w.vel[0, 0]) < 1e-6
    )  # equal masses swap velocity


def test_pendulum_period():
    L = 1.0
    r = P.simulate({"preset": "pendulum", "length": L, "angle": 5, "duration": 12})[
        "summary"
    ]
    assert (
        abs(r["period_s"] - 2 * math.pi * math.sqrt(L / 9.81))
        / (2 * math.pi * math.sqrt(L / 9.81))
        < 0.03
    )


def test_rod_length_held_double_pendulum():
    sim = P.simulate({"preset": "double_pendulum", "duration": 8})
    F = sim["frames"]
    assert np.abs(np.linalg.norm(F[:, 1] - F[:, 0], axis=1) - 1.0).max() < 0.02
    assert np.abs(np.linalg.norm(F[:, 2] - F[:, 1], axis=1) - 1.0).max() < 0.02


def test_figure_eight_stays_bounded_and_conserves():
    r = P.simulate({"preset": "three_body", "duration": 6.3259})["summary"]
    assert r["max_extent"] < 2.0 and r["energy_drift"] < 1e-3


def test_bouncing_balls_stay_in_box():
    sim = P.simulate({"preset": "bounce", "n": 10, "duration": 6})
    assert np.abs(sim["frames"][:, :, :2]).max() < 5.01
    assert sim["frames"][:, :, 2].min() > -1e-6


def test_custom_and_unknown():
    s = P.simulate(
        {
            "preset": "custom",
            "duration": 1,
            "bodies": [{"pos": [0, 0, 5], "radius": 0.1}],
        }
    )
    assert s["frames"].shape[1] == 1
    try:
        P.simulate({"preset": "nope"})
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_every_preset_runs():
    for name in P.PRESETS:
        if name in ("custom", "ode"):
            continue
        out = P.simulate({"preset": name, "duration": 0.5})
        assert np.isfinite(out["frames"]).all(), name


def test_ode_exponential_decay_exact():
    out = P.simulate(
        {
            "preset": "ode",
            "order": 1,
            "eq": ["-k*x"],
            "params": {"k": 0.5},
            "init": {"x": 2.0},
            "duration": 4,
            "dt": 0.01,
        }
    )
    assert abs(out["frames"][-1, 0, 0] - 2.0 * math.exp(-2.0)) < 1e-5


def test_ode_second_order_oscillator_energy_and_period():
    out = P.simulate(
        {
            "preset": "ode",
            "order": 2,
            "eq": ["-4*x"],
            "init": {"x": 1.0, "vx": 0.0},
            "duration": math.pi / 2,
            "dt": 1e-3,
        }
    )
    assert (
        abs(out["frames"][-1, 0, 0] - (-1.0)) < 1e-4
    )  # x = cos(2t); at t = pi/2 it is -1


def test_ode_rejects_unsafe_expressions():
    for bad in [
        "__import__('os').system('x')",
        "x.real",
        "open('f')",
        "[1,2]",
        "lambda: 1",
        "x if y else __builtins__",
    ]:
        try:
            P.simulate(
                {"preset": "ode", "eq": [bad], "init": {"x": 1.0}, "duration": 0.1}
            )
        except (ValueError, SyntaxError):
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_lorenz_stays_bounded_and_two_starts_diverge():
    out = P.simulate({"preset": "lorenz", "duration": 25})
    F = out["frames"]
    assert np.isfinite(F).all() and np.abs(F).max() < 80
    assert (
        np.linalg.norm(F[-1, 0] - F[-1, 1]) > 1.0
        and np.linalg.norm(F[0, 0] - F[0, 1]) < 0.01
    )


def test_random_walk_msd_matches_theory():
    r = P.simulate({"preset": "random_walk", "n": 400, "D": 0.5, "duration": 6})[
        "summary"
    ]
    assert abs(r["msd"] - r["expected_msd"]) / r["expected_msd"] < 0.15


def test_brownian_grain_moves_and_stays_in_box():
    sim = P.simulate({"preset": "brownian", "n": 80, "duration": 6})
    s = sim["summary"]
    assert s["path_length"] > s["net_displacement"] > 0
    assert np.abs(sim["frames"]).max() < 3.01
