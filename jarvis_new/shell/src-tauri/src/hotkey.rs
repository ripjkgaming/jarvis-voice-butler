//! Global hotkey: Super+J summons the overlay from anywhere.
//!
//! Wayland caveat (documented in the build plan): server-side global
//! shortcuts depend on the compositor portal. If `Super+J` fails to
//! register, we fall back to `Ctrl+Alt+J`, and the documented last resort
//! is a user-bound KWin shortcut invoking `jarvis talk` (single-instance
//! CLI arg, handled in main.rs).

use tauri::{AppHandle, Emitter};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

use crate::tray::{show_overlay, EVENT_TALK};

pub const PRIMARY_ACCEL: &str = "Super+J";
pub const FALLBACK_ACCEL: &str = "Ctrl+Alt+J";

/// Parse our two accelerator spellings. Pure (unit-testable without a
/// display server).
pub fn parse_accelerator(spec: &str) -> Option<Shortcut> {
    match spec {
        "Super+J" => Some(Shortcut::new(Some(Modifiers::SUPER), Code::KeyJ)),
        "Ctrl+Alt+J" => Some(Shortcut::new(
            Some(Modifiers::CONTROL | Modifiers::ALT),
            Code::KeyJ,
        )),
        _ => None,
    }
}

/// Register primary, else fallback. Returns the accelerator in effect.
pub fn register(app: &AppHandle) -> Result<&'static str, String> {
    let handler = |app: &AppHandle| {
        show_overlay(app);
        let _ = app.emit(EVENT_TALK, ());
    };
    let primary = parse_accelerator(PRIMARY_ACCEL).expect("primary parses");
    match app.global_shortcut().on_shortcut(primary, {
        let app = app.clone();
        move |_, _, _| handler(&app)
    }) {
        Ok(_) => Ok(PRIMARY_ACCEL),
        Err(first) => {
            let fallback = parse_accelerator(FALLBACK_ACCEL).expect("fallback parses");
            app.global_shortcut()
                .on_shortcut(fallback, {
                    let app = app.clone();
                    move |_, _, _| handler(&app)
                })
                .map_err(|e| format!("hotkey register failed ({first}; {e})"))?;
            Ok(FALLBACK_ACCEL)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn both_accelerators_parse() {
        assert!(parse_accelerator(PRIMARY_ACCEL).is_some());
        assert!(parse_accelerator(FALLBACK_ACCEL).is_some());
        assert!(parse_accelerator("F13").is_none());
    }
}
