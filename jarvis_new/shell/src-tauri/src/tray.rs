//! System tray: status dot + Talk/Toggle/Quit menu.
//!
//! The tray is the shell's face while the overlay is hidden. Status text
//! mirrors [`ShellState`]; Talk focuses the overlay (voice summon itself
//! stays hotword-driven until the Phase 4 wake_client control hook).

use tauri::{
    menu::{MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager,
};

use crate::health::ShellState::{self, Degraded, Down, Running, Starting};

/// Handle to the disabled status line, stashed in Tauri state at build
/// time so [`update_status`] never has to look the menu item up by id.
struct StatusHandle(tauri::menu::MenuItem<tauri::Wry>);

pub const EVENT_TALK: &str = "jarvis-talk";
pub const EVENT_TOGGLE: &str = "jarvis-toggle";

pub const ITEM_TALK: &str = "talk";
pub const ITEM_TOGGLE: &str = "toggle-overlay";
pub const ITEM_STATUS: &str = "status";
pub const ITEM_QUIT: &str = "quit";

/// Build the tray icon + menu. Pure Tauri wiring; state updates flow
/// through [`update_status`].
pub fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let talk = MenuItemBuilder::with_id(ITEM_TALK, "Talk to Jarvis").build(app)?;
    let toggle = MenuItemBuilder::with_id(ITEM_TOGGLE, "Show overlay").build(app)?;
    let status = MenuItemBuilder::with_id(ITEM_STATUS, "Status: starting…")
        .enabled(false)
        .build(app)?;
    let quit = MenuItemBuilder::with_id(ITEM_QUIT, "Quit").build(app)?;
    let menu = MenuBuilder::new(app)
        .items(&[&talk, &toggle, &status, &quit])
        .build()?;
    app.manage(StatusHandle(status));

    TrayIconBuilder::with_id("jarvis-tray")
        .tooltip("Jarvis")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id().as_ref() {
            ITEM_TALK => {
                show_overlay(app);
                let _ = app.emit(EVENT_TALK, ());
            }
            ITEM_TOGGLE => {
                let _ = app.emit(EVENT_TOGGLE, ());
            }
            ITEM_QUIT => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

/// Show + focus the overlay window. No-op when it is already visible.
pub fn show_overlay(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("overlay") {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// Toggle overlay visibility (hotkey + `jarvis toggle` share this).
pub fn toggle_overlay(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("overlay") {
        match win.is_visible() {
            Ok(true) => {
                let _ = win.hide();
            }
            _ => show_overlay(app),
        }
    }
}

/// Refresh the disabled status line from manager state.
pub fn update_status(app: &AppHandle, state: &ShellState) {
    let text = match state {
        Starting => "Status: … starting".to_string(),
        Running => "Status: ● listening".to_string(),
        Degraded(why) => format!("Status: ◐ degraded ({why})"),
        Down(why) => format!("Status: ○ down ({why})"),
    };
    if let Some(h) = app.try_state::<StatusHandle>() {
        let _ = h.0.set_text(&text);
    }
    if let Some(tray) = app.tray_by_id("jarvis-tray") {
        let _ = tray.set_tooltip(format!("Jarvis — {text}").into());
    }
}
