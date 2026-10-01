"""Behavioral monitor regressions for the actual Rust-generated KWin scripts.

Only pure script builders are compiled into a temporary helper. The emitted
JavaScript runs in Node with mocked KWin outputs/signals; no live D-Bus, display,
Tauri process, or user files are accessed. Run with uv run pytest tests/test_school_monitors.py.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "shell/src-tauri/src/school.rs"
HARNESS = Path(__file__).with_name("school_kwin_harness.cjs")


def _declaration(source: str, pattern: str) -> str:
    found = re.search(pattern + r".*?^}", source, re.MULTILINE | re.DOTALL)
    assert found, pattern
    return found.group(0)


@pytest.fixture(scope="module")
def scripts(tmp_path_factory):
    cargo, node = shutil.which("cargo"), shutil.which("node")
    if not cargo or not node:
        pytest.skip("behavioral KWin checks require Cargo and Node")
    source = SOURCE.read_text().split("#[cfg(test)]")[0]
    declarations = [
        _declaration(source, rf"^macro_rules! {name} ")
        for name in re.findall(r"^macro_rules! (\w+) ", source, re.MULTILINE)
    ]
    declarations.extend(
        re.findall(
            r"^(?:pub )?const \w+:[^\n]*?=.*?;\n", source, re.MULTILINE | re.DOTALL
        )
    )
    declarations.append(
        _declaration(source, r"^#\[derive\([^\n]+\)\]\npub enum Cover ")
    )
    for name in [
        "primary_script",
        "kwin_script",
        "keeper_script",
        "measure_script",
        "cover_script",
        "menu_script",
        "menu_script_on_primary",
    ]:
        declarations.append(_declaration(source, rf"^(?:pub )?fn {name}\("))
    main = r"""
fn main() {
    let args: Vec<String> = std::env::args().collect();
    let primary = if args[1].is_empty() { None } else { Some(args[1].as_str()) };
    let rect: [i32; 4] = std::array::from_fn(|i| args[i + 2].parse().unwrap());
    let scripts = serde_json::json!({
        "dock": kwin_script(true, None, primary),
        "keeper": keeper_script(primary, 0),
        "keeper_menu": keeper_script(primary, 480),
        "menu": menu_script_on_primary(480, primary),
        "menu_close": menu_script_on_primary(0, primary),
        "measure": measure_script("crossmonitor", primary),
        "cover_primary": cover_script(Cover::Primary, primary, None),
        "cover_here": cover_script(Cover::Here, primary, None),
        "cover_at": cover_script(Cover::At(rect), primary, None),
        "unframe": cover_script(Cover::Unframe, primary, Some(rect)),
        "restore": kwin_script(false, Some((rect[0], rect[1], rect[2] as u32, rect[3] as u32)), primary),
    });
    println!("{}", scripts);
}
"""
    project = tmp_path_factory.mktemp("school-script-builders")
    (project / "src").mkdir()
    (project / "Cargo.toml").write_text(
        '[package]\nname="school-script-tests"\nversion="0.0.0"\nedition="2021"\n'
        '[dependencies]\nserde_json="1"\n'
    )
    (project / "src/main.rs").write_text(
        "#![allow(dead_code)]\n" + "\n".join(declarations) + main
    )
    compiled = subprocess.run(
        [
            cargo,
            "build",
            "--offline",
            "--quiet",
            "--manifest-path",
            str(project / "Cargo.toml"),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert compiled.returncode == 0, compiled.stderr
    binary = project / "target/debug/school-script-tests"

    def emit(primary="DP-1", rect=(-1750, 180, 1280, 800)):
        result = subprocess.run(
            [str(binary), primary, *map(str, rect)],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return json.loads(result.stdout)

    return emit


def _run(scripts, steps, **options):
    setup = {
        "scripts": scripts,
        "outputs": [
            {
                "name": "DP-1",
                "geometry": {"x": 320, "y": 240, "width": 1920, "height": 1080},
                "panel": 64,
                "scale": 1.5,
            },
            {
                "name": "HDMI-A-1",
                "geometry": {"x": -1920, "y": 0, "width": 1920, "height": 1080},
                "panel": 48,
                "scale": 1,
            },
        ],
        "hud": {
            "output": "HDMI-A-1",
            "geometry": {"x": -1750, "y": 180, "width": 1280, "height": 800},
        },
        "active": "HDMI-A-1",
        "order": ["DP-1", "HDMI-A-1"],
        "steps": steps,
    }
    setup.update(options)
    result = subprocess.run(
        [shutil.which("node"), str(HARNESS)],
        input=json.dumps(setup),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _hud(snapshot):
    return snapshot["windows"]["hud"]


@pytest.mark.parametrize("script", ["dock", "keeper", "menu", "menu_close"])
def test_school_targets_main_even_while_hud_and_focus_are_secondary(scripts, script):
    result = _run(scripts(), [{"run": script}])[-1]
    hud = _hud(result)
    extra = 480 if script == "menu" else 0
    assert hud["output"] == "DP-1"
    assert hud["geometry"] == {
        "x": 320,
        "y": 1256 - extra,
        "width": 1920,
        "height": 64 + extra,
    }
    assert result["windows"]["other"]["writes"] == 0


def test_entry_and_return_cross_monitors_restore_exact_original_frame(scripts):
    results = _run(
        scripts(),
        [
            {"run": "measure"},
            {"run": "unframe"},
            {"run": "cover_here"},
            {"run": "cover_primary"},
            {"run": "dock"},
            {"run": "cover_at"},
            {"run": "restore"},
        ],
    )
    measure = results[0]["messages"][0]["value"]
    assert measure["hud"] == {"x": 170, "y": 180, "w": 1280, "h": 800}
    assert measure["primary"] == {"x": 320, "y": 240, "w": 1920, "h": 1080}
    assert measure["panel"] == 64
    assert measure["same"] is False and measure["dir"] == "right"
    assert _hud(results[2])["geometry"] == {
        "x": -1920,
        "y": 0,
        "width": 1920,
        "height": 1080,
    }
    assert _hud(results[3])["geometry"] == {
        "x": 320,
        "y": 240,
        "width": 1920,
        "height": 1080,
    }
    assert _hud(results[5])["output"] == "HDMI-A-1"
    restored = _hud(results[-1])
    assert restored["geometry"] == {"x": -1750, "y": 180, "width": 1280, "height": 800}
    assert not any(
        restored[key]
        for key in [
            "keepAbove",
            "noBorder",
            "skipTaskbar",
            "skipPager",
            "skipSwitcher",
            "onAllDesktops",
        ]
    )


@pytest.mark.parametrize(
    "script", ["dock", "keeper", "menu", "cover_primary", "measure"]
)
def test_live_primary_order_overrides_stale_kscreen_name(scripts, script):
    result = _run(scripts("DP-1"), [{"run": script}], order=["HDMI-A-1", "DP-1"])[-1]
    if script == "measure":
        assert result["messages"][0]["value"]["same"] is True
    else:
        assert _hud(result)["output"] == "HDMI-A-1"
        assert _hud(result)["geometry"]["x"] == -1920


def test_keeper_tracks_primary_change_without_losing_open_menu(scripts):
    results = _run(
        scripts(),
        [
            {"run": "keeper_menu"},
            {"order": ["HDMI-A-1", "DP-1"], "signal": "screenOrderChanged"},
        ],
    )
    assert _hud(results[0])["geometry"]["height"] == 544
    assert _hud(results[-1])["output"] == "HDMI-A-1"
    assert _hud(results[-1])["geometry"] == {
        "x": -1920,
        "y": 552,
        "width": 1920,
        "height": 528,
    }


def test_keeper_recovers_from_hotplug_and_layout_scale_change(scripts):
    results = _run(
        scripts(),
        [
            {"run": "keeper"},
            {"screens": ["HDMI-A-1"], "signal": "screensChanged"},
            {
                "layout": {
                    "name": "HDMI-A-1",
                    "geometry": {"x": -1600, "y": -900, "width": 1600, "height": 900},
                    "scale": 1.25,
                    "panel": 60,
                    "reserved": 60,
                },
                "signal": "virtualScreenGeometryChanged",
            },
        ],
    )
    assert _hud(results[1])["output"] == "HDMI-A-1"
    assert _hud(results[-1])["geometry"] == {
        "x": -1600,
        "y": -60,
        "width": 1600,
        "height": 60,
    }


def test_primary_fallback_uses_connector_then_stable_first_output(scripts):
    # Old KWin without screenOrder still respects kscreen's primary name.
    named = _run(scripts("DP-1"), [{"run": "dock"}], noScreenOrder=True)[-1]
    assert _hud(named)["output"] == "DP-1"
    # A temporarily unavailable primary lookup must not chase keyboard focus.
    unnamed = _run(scripts("disconnected"), [{"run": "dock"}], noScreenOrder=True)[-1]
    assert _hud(unnamed)["output"] == "DP-1"


def test_keeper_returns_accidentally_moved_taskbar_to_main(scripts):
    result = _run(
        scripts(),
        [
            {"run": "keeper"},
            {
                "move": {
                    "output": "HDMI-A-1",
                    "geometry": {"x": -1920, "y": 1032, "width": 1920, "height": 48},
                }
            },
        ],
    )[-1]
    assert _hud(result)["output"] == "DP-1"
    assert _hud(result)["geometry"] == {
        "x": 320,
        "y": 1256,
        "width": 1920,
        "height": 64,
    }


def test_floating_panel_insets_stay_in_target_logical_coordinates(scripts):
    result = _run(scripts(), [{"run": "dock"}], thickness=40)[-1]
    assert _hud(result)["geometry"] == {
        "x": 332,
        "y": 1268,
        "width": 1896,
        "height": 40,
    }


@pytest.mark.parametrize("script", ["dock", "keeper", "menu", "keeper_menu"])
def test_primary_without_panel_stays_primary_and_uses_slim_fallback(scripts, script):
    result = _run(
        scripts(),
        [{"run": script}],
        outputs=[
            {
                "name": "DP-1",
                "geometry": {"x": 320, "y": 240, "width": 1920, "height": 1080},
                "panel": 0,
            },
            {
                "name": "HDMI-A-1",
                "geometry": {"x": -1920, "y": 0, "width": 1920, "height": 1080},
                "panel": 48,
            },
        ],
    )[-1]
    extra = 480 if script in ("menu", "keeper_menu") else 0
    assert _hud(result)["output"] == "DP-1"
    assert _hud(result)["geometry"] == {
        "x": 1608,
        "y": 1260 - extra,
        "width": 620,
        "height": 48 + extra,
    }


@pytest.mark.parametrize(
    "script", ["dock", "keeper", "menu", "cover_primary", "measure"]
)
def test_temporary_no_outputs_does_not_move_or_crash(scripts, script):
    result = _run(scripts(), [{"screens": []}, {"run": script}])[-1]
    assert _hud(result)["writes"] == 0
    assert result["moves"] == []
