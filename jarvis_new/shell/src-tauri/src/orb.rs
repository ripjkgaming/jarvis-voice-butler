//! Legacy Brave-orb support. The implementation is retained for compatibility
//! with old shell commands, but it is intentionally disabled: application
//! switches must never change the main HUD's form or position.

use tauri::{AppHandle, Manager};

/// Requested size before KWin fits it to the panel (logical px).
pub const ORB_SIZE: f64 = 40.0;
/// Window title while docked; the KWin rule matches it exactly.
pub const ORB_TITLE: &str = "Jarvis \u{b7} Orb";
const RULE_ID: &str = "jarvis-brave-orb";

pub fn is_orb() -> bool {
    // The main HUD must never become an orb. Layout changes are reserved for
    // Study/School Mode; keep this false so stale `orbon`/`orboff` commands
    // cannot make the overlay flash, resize, or move.
    false
}

/// KWin rule: never take focus, stay above, stay out of switchers. Pure.
pub fn rule_entries() -> Vec<(&'static str, &'static str)> {
    vec![
        ("Description", "Jarvis Brave orb (click-through, no focus)"),
        ("title", ORB_TITLE),
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

/// KWin script docking the orb in the bottom panel. Pure.
///
/// The panel is the gap between the full screen and the maximize area.
/// The orb is centred horizontally on that gap and sized to fit it (a
/// little inset); with no bottom panel it sits in the bottom-right corner.
pub fn kwin_script() -> String {
    format!(
        "for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"{title}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           const full = workspace.clientArea(KWin.FullScreenArea, w);\n\
           const a = workspace.clientArea(KWin.MaximizeArea, w);\n\
           const panel = (full.y + full.height) - (a.y + a.height);\n\
           w.noBorder = true;\n\
           if (panel >= 24) {{\n\
             const s = Math.max(24, Math.min(44, panel - 6));\n\
             w.frameGeometry = {{x: full.x + Math.round((full.width - s) / 2), y: a.y + a.height + Math.round((panel - s) / 2), width: s, height: s}};\n\
           }} else {{\n\
             const s = 44;\n\
             w.frameGeometry = {{x: a.x + a.width - s - 12, y: a.y + a.height - s - 12, width: s, height: s}};\n\
           }}\n\
           w.keepAbove = true; w.skipTaskbar = true; w.skipPager = true; w.skipSwitcher = true; w.onAllDesktops = true;\n\
         }}\n",
        title = ORB_TITLE
    )
}

/// Shrink the overlay to the orb and show it without focus.
pub fn enter(app: &AppHandle) {
    let _ = app;
    // Deliberately disabled: switching applications must not alter the HUD.
}

/// Window properties + dock. Also used by show_overlay while in orb mode.
pub fn apply(app: &AppHandle) {
    crate::school::install_rule(RULE_ID, &rule_entries());
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        let _ = win.set_title(ORB_TITLE);
        let _ = win.set_min_size(None::<tauri::Size>);
        let _ = win.set_decorations(false);
        let _ = win.set_skip_taskbar(true);
        let _ = win.set_always_on_top(true);
        let _ = win.set_focusable(false);
        let _ = win.set_size(tauri::Size::Logical(tauri::LogicalSize {
            width: ORB_SIZE,
            height: ORB_SIZE,
        }));
        let _ = win.show();
        let _ = win.set_ignore_cursor_events(true);
    }
    std::thread::spawn(|| {
        // Let the compositor map the resized window before docking it.
        std::thread::sleep(std::time::Duration::from_millis(400));
        crate::school::run_kwin_named(&kwin_script(), "jarvis_orb_dock");
    });
}

/// Back to whatever Sir had before Brave: the HUD (visible or hidden at
/// its saved geometry), or the school strip if school mode came on.
pub fn exit(app: &AppHandle) {
    let _ = app;
    // Deliberately disabled: only Study/School Mode may change the HUD
    // geometry or presentation.
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn script_targets_only_the_orb_window() {
        let s = kwin_script();
        assert!(s.contains(ORB_TITLE));
        assert!(s.contains("resourceClass"));
        assert!(s.contains("FullScreenArea") && s.contains("MaximizeArea"));
    }

    #[test]
    fn rule_is_exact_title_and_forced() {
        let rule = rule_entries();
        assert!(rule.contains(&("title", ORB_TITLE)) && rule.contains(&("titlematch", "1")));
        assert!(rule.contains(&("acceptfocus", "false")) && rule.contains(&("acceptfocusrule", "2")));
    }

    #[test]
    fn orb_rule_id_differs_from_school() {
        let rules = crate::school::rules_with("jarvis-school-strip", RULE_ID);
        assert_eq!(rules, "jarvis-school-strip,jarvis-brave-orb");
    }
}
