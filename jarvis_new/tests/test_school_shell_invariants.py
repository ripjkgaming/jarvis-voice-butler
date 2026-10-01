"""Production wiring checks for school lifecycle safety, without a desktop.

Concurrency behavior is tested directly by school.rs's deterministic Rust tests:
delayed_return_cannot_remove_reentry_rule_or_move_strip, delayed_return_cannot_
overwrite_a_newer_return, delayed_school_stage_cannot_move_a_new_entry,
lifecycle_gate_holds_until_delayed_effect_finishes, and the one_shot_* tests.
Those replace the old mode-only guard and post-load keeper-unload assertions.
Run them with cargo test --no-default-features --bin jarvis-shell.

These complementary checks keep the real restore, boot, menu, keeper and rule
callers connected to those tested safety boundaries. They do not claim to test
KWin, Tauri dispatch, or concurrency by inspecting strings. Generated monitor
placement behavior is separately exercised by test_school_monitors.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CODE = (
    (
        Path(__file__).resolve().parent.parent
        / "shell"
        / "src-tauri"
        / "src"
        / "school.rs"
    )
    .read_text()
    .split("#[cfg(test)]")[0]
)


def _mask_literals(source: str) -> str:
    # Preserve offsets while ignoring delimiters in ordinary Rust strings and
    # comments. The declarations inspected here have no raw-string literals.
    return re.sub(
        r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/',
        lambda match: " " * len(match.group()),
        source,
        flags=re.S,
    )


def _group_end(source: str, start: int) -> int:
    masked = _mask_literals(source)
    closing = {"(": ")", "{": "}", "[": "]"}
    stack = []
    for index in range(start, len(masked)):
        token = masked[index]
        if token in closing:
            stack.append(closing[token])
        elif token in closing.values():
            assert stack and token == stack.pop(), source[start:index]
            if not stack:
                return index
    raise AssertionError(f"Unclosed source group at {start}")


def _fn_body(name: str) -> str:
    match = re.search(rf"\bfn {re.escape(name)}\b", _mask_literals(CODE))
    assert match, name
    start = _mask_literals(CODE).index("{", match.end())
    return CODE[start + 1 : _group_end(CODE, start)]


def _calls(source: str, name: str) -> list[list[str]]:
    masked = _mask_literals(source)
    calls = []
    for match in re.finditer(rf"\b{re.escape(name)}\s*\(", masked):
        start = match.end()
        end = _group_end(source, start - 1)
        arguments, previous, index = [], start, start
        while index < end:
            if masked[index] in "({[":
                index = _group_end(source, index)
            elif masked[index] == ",":
                arguments.append(source[previous:index].strip())
                previous = index + 1
            index += 1
        if source[previous:end].strip():
            arguments.append(source[previous:end].strip())
        calls.append(arguments)
    return calls


def _call(source: str, name: str) -> list[str]:
    calls = _calls(source, name)
    assert len(calls) == 1, (name, calls)
    return calls[0]


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"//[^\n]*", "", source))


def test_delayed_restore_guards_rule_removal_and_placement_together() -> None:
    body = _fn_body("restore_at")
    assert body.index("TX_GEN.fetch_add") < body.index("std::thread::spawn")
    script, _, current, before_load = _call(body, "run_guarded_kwin")
    assert _compact(script) == "&kwin_script(false,restore,None)"
    assert _compact(current) == "||current_mode(false,gen)"
    assert _compact(before_load) == "||uninstall_rule(RULE_ID)"
    assert len(_calls(body, "uninstall_rule")) == 1
    assert _call(body, "heal_normal_generation") == ["Some(&app)", "gen"]
    assert "NORMALIZE_PASSES_MS" in body
    for property_call in (
        "set_title(HUD_TITLE)",
        "set_always_on_top(false)",
        "set_decorations(true)",
        "set_focusable(true)",
        "set_skip_taskbar(false)",
    ):
        assert property_call in body


def test_normalization_and_boot_keep_the_requested_generation() -> None:
    heal = _fn_body("heal_normal_generation")
    script, _, current, before_load = _call(heal, "run_guarded_kwin")
    assert script == "&normalize_script()"
    assert _compact(current) == "||current_mode(false,gen)"
    assert _compact(before_load) == "||uninstall_rule(RULE_ID)"
    main_callback = _call(heal, "on_main_sync")[1]
    assert main_callback.index("current_mode(false, gen)") < main_callback.index(
        "set_always_on_top(false)"
    )
    boot = _fn_body("fill_work_area_at_boot")
    assert boot.index("TX_GEN.load") < boot.index("std::thread::spawn")
    assert _call(boot, "heal_normal_generation") == ["None", "gen"]
    for school, generation, effect in _calls(boot, "run_in_mode"):
        assert (school, generation) == ("false", "gen")
        assert "BOOT_FILL_NAME" in effect
    assert len(_calls(boot, "run_in_mode")) == 2  # load and delayed unload


@pytest.mark.parametrize(
    "entry", ["enter", "enter_quiet", "apply", "exit", "school_stage", "suit_focus"]
)
def test_native_window_entry_points_dispatch_through_the_main_thread_gate(
    entry: str,
) -> None:
    assert len(_calls(_fn_body(entry), "on_main_sync")) == 1
    dispatch = _call(_fn_body("on_main_sync"), "app.run_on_main_thread")[0]
    assert dispatch.index("LIFECYCLE.lock()") < dispatch.index("action(&handle)")


@pytest.mark.parametrize("entry", ["apply_locked", "school_stage_locked"])
def test_delayed_dock_cannot_overwrite_a_new_menu_expansion(entry: str) -> None:
    body = _fn_body(entry)
    script, _, current, _ = _call(body, "run_guarded_kwin")
    assert "kwin_script(true" in script
    assert "current_mode(true,gen)" in _compact(current)
    assert "MENU_EXTRA_NOW.load(Ordering::SeqCst)==0" in _compact(current)
    assert not _calls(body, "run_kwin_named")


def test_cover_and_menu_deferred_effects_check_their_original_generation() -> None:
    stage = _fn_body("school_stage_locked")
    assert stage.index("TX_GEN.load") < stage.index("std::thread::spawn")
    for arguments in _calls(stage, "run_school_stage_kwin"):
        assert arguments[-1] == "gen"
    assert len(_calls(stage, "run_school_stage_kwin")) == 2  # measure and cover
    stage_guard = _call(_fn_body("run_school_stage_kwin"), "run_guarded_kwin")
    assert _compact(stage_guard[2]) == "||current_mode(true,gen)"
    menu = _fn_body("school_menu")
    assert menu.index("TX_GEN.load") < menu.index("std::thread::spawn")
    guard = _compact(_call(menu, "run_guarded_kwin")[2])
    assert (
        guard
        == "||current_mode(true,gen)&&!in_transition()&&MENU_EXTRA_NOW.load(Ordering::SeqCst)==extra"
    )


def test_keeper_checks_both_owners_under_the_lifecycle_gate_before_loading() -> None:
    keeper = _fn_body("start_keeper")
    load = keeper.index("load_kwin_named")
    assert keeper.index("LIFECYCLE.lock()") < keeper.index("KEEPER_LOCK.lock()") < load
    for stale_check in (
        "!is_school()",
        "in_transition()",
        "TX_GEN.load(Ordering::SeqCst) != gen",
        "KEEPER_GEN.load(Ordering::SeqCst) != keeper_gen",
    ):
        assert keeper.index(stale_check) < load
    stop = _fn_body("stop_keeper")
    assert (
        stop.index("KEEPER_GEN.fetch_add")
        < stop.index("KEEPER_LOCK.lock()")
        < stop.index("unload_kwin_named")
    )


def test_one_shot_retention_is_outside_guarded_native_start() -> None:
    body = _fn_body("run_guarded_kwin")
    _, current, start = _call(body, "LIFECYCLE.start_one_shot")
    assert current == "current"
    assert start.index("before_load()") < start.index("load_kwin_named(script, name)")
    assert "finish" not in start and "sleep" not in start
    assert body.index("script.finish()") > body.index(start) + len(start)
    # Production cleanup uses the invocation's identity for both resources.
    finish = _fn_body("finish")
    assert "unload_kwin_named(name)" in finish
    assert 'format!("{name}.js")' in finish and "remove_file" in finish


@pytest.mark.parametrize("operation", ["install_rule", "uninstall_rule"])
def test_rule_list_read_modify_write_uses_one_shared_mutex(operation: str) -> None:
    gate, mutation = _call(_fn_body(operation), "mutate_rule_config")
    assert gate == "&RULES_MUTATION"
    assert f"{operation}_locked(" in mutation
    # Reading, writing and reloading must all remain inside that transaction.
    body = _fn_body(f"{operation}_locked")
    assert (
        body.index("kreadconfig6")
        < body.index("kwriteconfig6")
        < body.index("org.kde.KWin.reconfigure")
    )
