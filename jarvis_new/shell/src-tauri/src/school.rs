//! School mode: the overlay becomes a slim, click-through strip docked at
//! the bottom-right of the working area, above other windows.
//!
//! The strip must never get in the way: no decorations, no taskbar entry,
//! never takes keyboard focus (`set_focusable(false)`) and ignores the
//! mouse (`set_ignore_cursor_events(true)`), so Sir keeps typing and
//! clicking through it. Wayland gives clients no say over position, so a
//! one-shot KWin script docks it (and restores the saved HUD geometry on
//! exit). Mode state lives in `~/.jarvis/mode.json`, written by the bridge;
//! `jarvis-shell schoolon|schooloff` applies it live.

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{AppHandle, Manager};

/// Strip size in logical pixels (one status row + a caption line).
pub const STRIP_WIDTH: f64 = 620.0;
pub const STRIP_HEIGHT: f64 = 64.0;
/// Gap from the working area's bottom-right corner (logical px).
pub const STRIP_MARGIN: i32 = 12;

/// Window title while in school mode: the KWin rule below matches it
/// exactly, so it never touches the normal HUD ("Jarvis").
pub const STRIP_TITLE: &str = "Jarvis \u{b7} School";
pub const HUD_TITLE: &str = "Jarvis";
const RULE_ID: &str = "jarvis-school-strip";

static SCHOOL: AtomicBool = AtomicBool::new(false);

pub fn is_school() -> bool {
    SCHOOL.load(Ordering::SeqCst)
}

/// Mode recorded on disk ("school" → true). Missing/corrupt → false.
pub fn school_on_disk(home: &std::path::Path) -> bool {
    std::fs::read_to_string(home.join("mode.json"))
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("mode").and_then(|m| m.as_str()).map(|m| m == "school"))
        .unwrap_or(false)
}

/// KWin script that docks the strip (school) or puts the HUD back.
/// Matches our overlay by exact caption "Jarvis" on a jarvis-shell window.
pub fn kwin_script(school: bool, restore: Option<(i32, i32, u32, u32)>) -> String {
    let body = if school {
        format!(
            "const a = workspace.clientArea(KWin.MaximizeArea, w);\n\
             const s = w.frameGeometry;\n\
             w.noBorder = true;\n\
             w.frameGeometry = {{x: a.x + a.width - s.width - {m}, y: a.y + a.height - s.height - {m}, width: s.width, height: s.height}};\n\
             w.keepAbove = true; w.skipTaskbar = true; w.skipPager = true; w.skipSwitcher = true; w.onAllDesktops = true;",
            m = STRIP_MARGIN
        )
    } else {
        let geo = match restore {
            Some((x, y, width, height)) => format!(
                "w.frameGeometry = {{x: {x}, y: {y}, width: {width}, height: {height}}};"
            ),
            None => String::new(),
        };
        format!(
            "w.noBorder = false;\n{geo}\n\
             w.skipTaskbar = false; w.skipPager = false; w.skipSwitcher = false; w.onAllDesktops = false;"
        )
    };
    format!(
        "for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"Jarvis\" && w.caption !== \"{title}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           {body}\n\
         }}\n",
        title = STRIP_TITLE
    )
}

/// `kwriteconfig6` args for the strip's window rule. Pure.
///
/// Scripts can't make a window unfocusable; a rule can. Matches only the
/// exact school title, forcing: never take focus, keep above, and stay
/// out of the taskbar / switcher / pager.
pub fn rule_entries() -> Vec<(&'static str, &'static str)> {
    vec![
        ("Description", "Jarvis school strip (click-through, no focus)"),
        ("title", STRIP_TITLE),
        ("titlematch", "1"),
        ("acceptfocus", "false"),
        ("acceptfocusrule", "2"),
        ("above", "true"),
        ("aboverule", "2"),
        ("skiptaskbar", "true"),
        ("skiptaskbarrule", "2"),
        ("skipswitcher", "true"),
        ("skipswitcherrule", "2"),
        ("skippager", "true"),
        ("skippagerrule", "2"),
    ]
}

/// Rules list with ours appended once. Pure.
pub fn rules_with_ours(existing: &str) -> String {
    let mut ids: Vec<&str> = existing.split(',').map(str::trim).filter(|s| !s.is_empty()).collect();
    if !ids.contains(&RULE_ID) {
        ids.push(RULE_ID);
    }
    ids.join(",")
}

/// Install the rule (idempotent) and ask KWin to reload rules. Fail-soft.
fn ensure_rule() {
    let read = std::process::Command::new("kreadconfig6")
        .args(["--file", "kwinrulesrc", "--group", "General", "--key", "rules"])
        .output();
    let existing = match read {
        Ok(o) => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        Err(_) => return, // not Plasma
    };
    let write = |group: &str, key: &str, value: &str| {
        let _ = std::process::Command::new("kwriteconfig6")
            .args(["--file", "kwinrulesrc", "--group", group, "--key", key, value])
            .output();
    };
    for (key, value) in rule_entries() {
        write(RULE_ID, key, value);
    }
    let rules = rules_with_ours(&existing);
    write("General", "count", &rules.split(',').count().to_string());
    write("General", "rules", &rules);
    let _ = std::process::Command::new("dbus-send")
        .args(["--session", "--dest=org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"])
        .output();
}

/// Load + run + unload a KWin script over D-Bus. Fail-soft (X11 / no KWin).
fn run_kwin(script: &str) {
    let path = std::env::temp_dir().join("jarvis-school-dock.js");
    if std::fs::write(&path, script).is_err() {
        return;
    }
    let name = "jarvis_school_dock";
    let dbus = |args: &[&str]| {
        std::process::Command::new("dbus-send")
            .args(["--session", "--print-reply", "--dest=org.kde.KWin"])
            .args(args)
            .output()
    };
    let _ = dbus(&["/Scripting", "org.kde.kwin.Scripting.unloadScript", &format!("string:{name}")]);
    let out = match dbus(&[
        "/Scripting",
        "org.kde.kwin.Scripting.loadScript",
        &format!("string:{}", path.display()),
        &format!("string:{name}"),
    ]) {
        Ok(o) => String::from_utf8_lossy(&o.stdout).to_string(),
        Err(_) => return,
    };
    let id = out
        .split_whitespace()
        .skip_while(|t| *t != "int32")
        .nth(1)
        .and_then(|t| t.parse::<i64>().ok());
    if let Some(id) = id {
        let _ = dbus(&[&format!("/Scripting/Script{id}"), "org.kde.kwin.Script.run"]);
    }
    std::thread::sleep(std::time::Duration::from_millis(300));
    let _ = dbus(&["/Scripting", "org.kde.kwin.Scripting.unloadScript", &format!("string:{name}")]);
}

/// Turn the overlay into the strip and show it (without taking focus).
pub fn enter(app: &AppHandle) {
    SCHOOL.store(true, Ordering::SeqCst);
    apply(app);
}

/// Window properties + dock for the strip. Also used by show_overlay.
pub fn apply(app: &AppHandle) {
    ensure_rule();
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        let _ = win.set_title(STRIP_TITLE);
        // tauri.conf.json gives the HUD a 960x540 minimum; the strip is tiny.
        let _ = win.set_min_size(None::<tauri::Size>);
        let _ = win.set_decorations(false);
        let _ = win.set_skip_taskbar(true);
        let _ = win.set_always_on_top(true);
        let _ = win.set_focusable(false);
        let _ = win.set_size(tauri::Size::Logical(tauri::LogicalSize {
            width: STRIP_WIDTH,
            height: STRIP_HEIGHT,
        }));
        let _ = win.show();
        let _ = win.set_ignore_cursor_events(true);
    }
    std::thread::spawn(|| {
        // Let the compositor map the resized window before docking it.
        std::thread::sleep(std::time::Duration::from_millis(400));
        run_kwin(&kwin_script(true, None));
    });
}

/// Back to the normal HUD at its saved geometry.
pub fn exit(app: &AppHandle) {
    SCHOOL.store(false, Ordering::SeqCst);
    let geom = crate::overlay::load_geometry(&crate::overlay::geometry_path(
        &crate::env_cfg::jarvis_home(),
    ));
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        let _ = win.set_ignore_cursor_events(crate::commands::click_through_desired());
        let _ = win.set_title(HUD_TITLE);
        let _ = win.set_min_size(Some(tauri::Size::Logical(tauri::LogicalSize {
            width: 960.0,
            height: 540.0,
        })));
        let _ = win.set_focusable(true);
        let _ = win.set_decorations(true);
        let _ = win.set_skip_taskbar(false);
        let (w, h) = geom
            .as_ref()
            .map(|g| (g.width, g.height))
            .unwrap_or((crate::overlay::OVERLAY_WIDTH, crate::overlay::OVERLAY_HEIGHT));
        let _ = win.set_size(tauri::Size::Physical(tauri::PhysicalSize { width: w, height: h }));
    }
    let restore = geom.map(|g| (g.x, g.y, g.width, g.height));
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(400));
        run_kwin(&kwin_script(false, restore));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disk_mode_parses() {
        let tmp = std::env::temp_dir().join("jarvis-school-test");
        std::fs::create_dir_all(&tmp).ok();
        std::fs::write(tmp.join("mode.json"), r#"{"mode": "school", "since": 1}"#).ok();
        assert!(school_on_disk(&tmp));
        std::fs::write(tmp.join("mode.json"), r#"{"mode": "normal"}"#).ok();
        assert!(!school_on_disk(&tmp));
        std::fs::write(tmp.join("mode.json"), "{broken").ok();
        assert!(!school_on_disk(&tmp));
        std::fs::remove_dir_all(&tmp).ok();
        assert!(!school_on_disk(&tmp));
    }

    #[test]
    fn scripts_target_only_our_window() {
        let dock = kwin_script(true, None);
        assert!(dock.contains("w.caption !== \"Jarvis\""));
        assert!(dock.contains("KWin.MaximizeArea") && dock.contains("keepAbove = true"));
        let back = kwin_script(false, Some((10, 20, 1280, 720)));
        assert!(back.contains("x: 10, y: 20, width: 1280, height: 720"));
        assert!(back.contains("skipTaskbar = false"));
        assert!(!kwin_script(false, None).contains("frameGeometry ="));
        assert!(dock.contains(STRIP_TITLE));
    }

    #[test]
    fn rule_is_exact_title_and_forced() {
        let rule = rule_entries();
        assert!(rule.contains(&("title", STRIP_TITLE)) && rule.contains(&("titlematch", "1")));
        assert!(rule.contains(&("acceptfocus", "false")) && rule.contains(&("acceptfocusrule", "2")));
        assert_eq!(rules_with_ours(""), RULE_ID);
        assert_eq!(rules_with_ours("abc"), format!("abc,{RULE_ID}"));
        assert_eq!(rules_with_ours(&format!("abc,{RULE_ID}")), format!("abc,{RULE_ID}"));
    }
}
