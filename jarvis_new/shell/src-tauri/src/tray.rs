//! System tray: status dot + Talk/Toggle/Quit menu.
//!
//! The tray is the shell's face while the overlay is hidden. Status text
//! mirrors [`ShellState`]; Talk focuses the overlay (voice summon itself
//! stays hotword-driven until the Phase 4 wake_client control hook).

use tauri::{
    menu::{CheckMenuItemBuilder, MenuBuilder, MenuItemBuilder},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager,
};

use crate::health::ShellState::{self, Degraded, Down, Running, Starting};

/// Handle to the disabled status line, stashed in Tauri state at build
/// time so [`update_status`] never has to look the menu item up by id.
struct StatusHandle(tauri::menu::MenuItem<tauri::Wry>);

/// Handle to the mute checkbox so the HUD/CLI mute paths can mirror state.
struct MuteHandle(tauri::menu::CheckMenuItem<tauri::Wry>);

/// Handle to the disabled updates line for the v1 checks-only updater.
struct UpdatesHandle(tauri::menu::MenuItem<tauri::Wry>);

pub const EVENT_TALK: &str = "jarvis-talk";
pub const EVENT_TOGGLE: &str = "jarvis-toggle";

pub const ITEM_TALK: &str = "talk";
pub const ITEM_TOGGLE: &str = "toggle-overlay";
pub const ITEM_PROJECTS: &str = "projects";
pub const ITEM_MUTE: &str = "mute-mic";
pub const ITEM_STATUS: &str = "status";
pub const ITEM_UPDATES: &str = "updates-note";
pub const ITEM_CHECK_UPDATES: &str = "check-updates";
pub const ITEM_QUIT: &str = "quit";

/// Build the tray icon + menu. Pure Tauri wiring; state updates flow
/// through [`update_status`].
pub fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let talk = MenuItemBuilder::with_id(ITEM_TALK, "Talk to Jarvis").build(app)?;
    let toggle = MenuItemBuilder::with_id(ITEM_TOGGLE, "Show overlay").build(app)?;
    let projects = MenuItemBuilder::with_id(ITEM_PROJECTS, "Project archive").build(app)?;
    let mute = CheckMenuItemBuilder::with_id(ITEM_MUTE, "Mute microphone")
        .checked(false)
        .build(app)?;
    let status = MenuItemBuilder::with_id(ITEM_STATUS, "Status: starting…")
        .enabled(false)
        .build(app)?;
    let updates = MenuItemBuilder::with_id(ITEM_UPDATES, "Updates: not checked")
        .enabled(false)
        .build(app)?;
    let check_updates =
        MenuItemBuilder::with_id(ITEM_CHECK_UPDATES, "Check for updates").build(app)?;
    let quit = MenuItemBuilder::with_id(ITEM_QUIT, "Quit").build(app)?;
    let menu = MenuBuilder::new(app)
        .items(&[
            &talk,
            &toggle,
            &projects,
            &mute,
            &status,
            &updates,
            &check_updates,
            &quit,
        ])
        .build()?;
    app.manage(StatusHandle(status));
    app.manage(MuteHandle(mute));
    app.manage(UpdatesHandle(updates));

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
            ITEM_PROJECTS => show_projects(app),
            ITEM_MUTE => {
                toggle_mute(app);
            }
            ITEM_CHECK_UPDATES => {
                let handle = app.clone();
                tauri::async_runtime::spawn(async move {
                    let (note, notify) =
                        crate::updater::note_for(&crate::updater::check_for_update(&handle).await);
                    crate::updater::set_updates_note(&handle, &note);
                    if notify {
                        desktop_notify("Jarvis update available", &note);
                    }
                });
            }
            ITEM_QUIT => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

/// Show + focus the overlay window. No-op when it is already visible.
/// Also enforces the 16:9 HUD size: a stale portrait geometry (or a user
/// resize below usability) grows back to 1280x720 in place. Fail-soft.
/// A never-placed window (persisted position still 0,0 — nobody dragged
/// it yet) is centered on the current screen instead: (0,0) hides under
/// panels on some KWin layouts and Wayland gives clients no say anyway.
pub fn show_overlay(app: &AppHandle) {
    if crate::school::is_school() {
        // The strip: shown without focus, click-through, docked.
        crate::school::apply(app);
        return;
    }
    if crate::orb::is_orb() {
        // Brave orb: a summon keeps the docked orb (it animates in place).
        crate::orb::apply(app);
        return;
    }
    if let Some(win) = app.get_webview_window("overlay") {
        if let Ok(size) = win.outer_size() {
            if size.width < 1000 || size.height < 600 {
                let _ = win.set_size(tauri::Size::Physical(tauri::PhysicalSize {
                    width: crate::overlay::OVERLAY_WIDTH,
                    height: crate::overlay::OVERLAY_HEIGHT,
                }));
            }
        }
        let path = crate::overlay::geometry_path(&crate::env_cfg::jarvis_home());
        let never_placed = match crate::overlay::load_geometry(&path) {
            Some(g) => g.x == 0 && g.y == 0,
            None => true,
        };
        let _ = win.show();
        if never_placed {
            let _ = win.center();
        }
        let _ = win.set_focus();
        // The window exists now: apply any click-through the HUD asked for
        // while it was hidden (see commands::set_overlay_click_through).
        crate::commands::apply_click_through(app);
    }
}

/// Reveal the simulated suit panel's host without starting a voice turn.
/// School menus already own their expanded geometry: showing must neither
/// re-dock them nor move them away from the primary display.
pub fn show_suit_overlay(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        if crate::school::is_school() {
            let _ = win.unminimize();
            let _ = win.show();
            return;
        }
        crate::orb::restore_for_suit(app);
        let _ = win.unminimize();
        show_overlay(app);
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

/// Toggle the God's-Eye globe window (`jarvis globe` / voice "launch
/// gods eye view"). Display-only: the page itself runs ?ambient=1
/// (auto-spin, pointer-events off), so show/hide is the only control.
pub fn toggle_globe(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("globe") {
        match win.is_visible() {
            Ok(true) => {
                let _ = win.hide();
            }
            _ => show_globe(app),
        }
    }
}

/// Show + focus the globe window (voice launch is show-only: saying
/// "launch" twice must never hide it out from under Sir).
pub fn show_globe(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("globe") {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// Toggle the project archive window (`jarvis projects`): research and
/// coding jobs the bridge runs in the background.
pub fn toggle_projects(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("projects") {
        match win.is_visible() {
            Ok(true) => {
                let _ = win.hide();
            }
            _ => show_projects(app),
        }
    }
}

/// Show + focus the project archive (voice "open my projects" is
/// show-only, like the globe: asking twice never hides it).
pub fn show_projects(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("projects") {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// Hide the project archive ("close research projects").
pub fn hide_projects(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("projects") {
        let _ = win.hide();
    }
}

/// Show + focus the drafts window (voice "open my drafts", only when a
/// reply draft is waiting; the bridge checks before it asks).
pub fn show_drafts(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("drafts") {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// Hide the drafts window ("close drafts", or the last draft was handled).
pub fn hide_drafts(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("drafts") {
        let _ = win.hide();
    }
}

/// Mirror a confirmed mute on the tray checkbox (HUD/CLI mute paths call
/// this; no bridge I/O here). Missing handle = no-op.
pub fn set_mute_checked(app: &AppHandle, checked: bool) {
    if let Some(h) = app.try_state::<MuteHandle>() {
        let _ = h.0.set_checked(checked);
    }
}

/// Update the disabled updates line (v1 checks-only updater). Missing
/// handle = no-op.
pub fn set_updates_text(app: &AppHandle, text: &str) {
    if let Some(h) = app.try_state::<UpdatesHandle>() {
        let _ = h.0.set_text(text);
    }
}

/// Tray checkbox handler: flip the mute via the bridge on a worker thread
/// (menu callbacks must not block), then mirror the confirmed state.
/// Failures leave the checkbox untouched — the wake listener is down.
fn toggle_mute(app: &AppHandle) {
    let current = app
        .try_state::<MuteHandle>()
        .and_then(|h| h.0.is_checked().ok())
        .unwrap_or(false);
    let want = !current;
    let handle = app.clone();
    std::thread::spawn(move || match crate::commands::post_mic_muted(want) {
        Ok(confirmed) => {
            crate::commands::set_desired_mute(&handle, confirmed);
            set_mute_checked(&handle, confirmed);
        }
        Err(e) => eprintln!("jarvis: tray mute failed ({e})"),
    });
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

/// Best-effort desktop notification (sidecar death, diagnostics ready).
/// Uses `notify-send` so the shell gains no new dependency; a missing
/// binary is a silent no-op because the tray state always carries the
/// same information. Blocking `output()` reaps the child; notify-send
/// fails fast with no daemon.
pub fn desktop_notify(title: &str, body: &str) {
    let _ = std::process::Command::new("notify-send")
        .args(["-u", "critical", "-a", "Jarvis", title, body])
        .output();
}
