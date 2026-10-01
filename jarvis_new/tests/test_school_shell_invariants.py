"""Source invariants for the shell's school mode (shell/src-tauri/src/school.rs).

The shell can't be built in CI here, so these pin the rules that stop the
"stuck always-on-top after leaving school mode" bug from coming back:

1. Any KWin script that pins the window above everything (the dock, cover
   and strip scripts) runs through run_school_kwin, which refuses to run
   once school mode is off. A late one used to re-pin the HUD after the
   restore had cleared it.
2. Leaving school drops the forcing window rule and re-clears the pin
   several times (heal_normal_mode) after the restore script.
3. The HUD also heals at boot, so a crash in school mode can't leave it pinned.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = (
    Path(__file__).resolve().parent.parent / "shell" / "src-tauri" / "src" / "school.rs"
).read_text()
# Everything before the #[cfg(test)] module.
CODE = SRC.split("#[cfg(test)]")[0]


def _fn_body(name: str) -> str:
    start = CODE.index(f"fn {name}(")
    brace = CODE.index("{", CODE.index(")", start))
    depth = 0
    for i in range(brace, len(CODE)):
        depth += CODE[i] == "{"
        depth -= CODE[i] == "}"
        if depth == 0:
            return CODE[brace : i + 1]
    raise AssertionError(name)


def test_pinning_scripts_only_run_through_the_school_guard() -> None:
    # No direct run_kwin_named / run_kwin of a school-mode (pinning) script.
    for m in re.finditer(r"run_kwin(?:_named)?\(([^;]*?)\);", CODE, re.S):
        call = m.group(1)
        if "kwin_script(true" in call or "cover_script(" in call:
            raise AssertionError(f"unguarded school script: {call[:80]!r}")
    assert "kwin_script(true" in CODE and "run_school_kwin(" in CODE
    assert "if !is_school()" in _fn_body("run_school_kwin")


def test_leaving_school_clears_every_pin() -> None:
    body = _fn_body("restore_at")
    assert "SCHOOL.store(false" in body
    assert "uninstall_rule(RULE_ID)" in body  # the forced keep-above rule
    assert "kwin_script(false" in body  # the restore script
    assert "heal_normal_mode" in body and "NORMALIZE_PASSES_MS" in body
    assert "set_always_on_top(false)" in body  # Tauri's own flag


def test_heal_only_runs_in_normal_mode_and_unpins() -> None:
    body = _fn_body("heal_normal_mode")
    assert body.index("is_school()") < body.index("normalize_script")
    assert "uninstall_rule" in body and "set_always_on_top(false)" in body
    assert "keepAbove = false" in _fn_body("normalize_script")


def test_hud_heals_at_boot() -> None:
    assert "heal_normal_mode(None)" in _fn_body("fill_work_area_at_boot")


def test_keeper_cannot_outlive_school_mode() -> None:
    body = _fn_body("start_keeper")
    assert "unload_kwin_named(KEEPER_NAME)" in body
