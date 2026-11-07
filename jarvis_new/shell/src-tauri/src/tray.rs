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
pub const ITEM_SCREEN_STOP: &str = "screen-stop";
pub const ITEM_SCREEN_RESUME: &str = "screen-resume-quiet";
pub const ITEM_SCREEN_END: &str = "screen-end";
pub const ITEM_WATCH_START: &str = "watch-start";
pub const ITEM_WATCH_CONFIRM: &str = "watch-confirm";
pub const ITEM_WATCH_STOP: &str = "watch-stop";
pub const ITEM_WATCH_STATUS: &str = "watch-status";
pub const ITEM_WATCH_BRIEFING: &str = "watch-briefing";
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
    let screen_enabled = crate::screen_control::configured();
    let screen_stop = MenuItemBuilder::with_id(ITEM_SCREEN_STOP, "Stop screen voice")
        .enabled(screen_enabled)
        .build(app)?;
    let screen_resume =
        MenuItemBuilder::with_id(ITEM_SCREEN_RESUME, "Resume screen voice — speech is quiet")
            .enabled(screen_enabled)
            .build(app)?;
    let screen_end = MenuItemBuilder::with_id(ITEM_SCREEN_END, "End screen call")
        .enabled(screen_enabled)
        .build(app)?;
    let watch_start = MenuItemBuilder::with_id(ITEM_WATCH_START, "Watch selected window — review Gemini disclosure").build(app)?;
    let watch_confirm = MenuItemBuilder::with_id(ITEM_WATCH_CONFIRM, "Confirm selected-window screenshots to Gemini").build(app)?;
    let watch_stop = MenuItemBuilder::with_id(ITEM_WATCH_STOP, "Stop Watch Mode").build(app)?;
    let watch_status = MenuItemBuilder::with_id(ITEM_WATCH_STATUS, "Watch Mode status").build(app)?;
    let watch_briefing = MenuItemBuilder::with_id(ITEM_WATCH_BRIEFING, "Watch Mode briefing — private window").build(app)?;
    let status = MenuItemBuilder::with_id(ITEM_STATUS, "Status: starting…")
        .enabled(false)
        .build(app)?;
    let updates = MenuItemBuilder::with_id(ITEM_UPDATES, "Updates: not checked")
        .enabled(false)
        .build(app)?;
    let check_updates =
        MenuItemBuilder::with_id(ITEM_CHECK_UPDATES, "Check for updates").build(app)?;
    let quit = MenuItemBuilder::with_id(ITEM_QUIT, "Quit").build(app)?;
    let menu = if crate::screen_control::native_only() {
        MenuBuilder::new(app)
            .items(&[&screen_stop, &screen_resume, &screen_end, &quit])
            .build()?
    } else {
        MenuBuilder::new(app)
            .items(&[
                &talk,
                &toggle,
                &projects,
                &mute,
                &screen_stop,
                &screen_resume,
                &screen_end,
                &watch_start,
                &watch_confirm,
                &watch_stop,
                &watch_status,
                &watch_briefing,
                &status,
                &updates,
                &check_updates,
                &quit,
            ])
            .build()?
    };
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
            ITEM_SCREEN_STOP => native_screen_control(crate::screen_control::Action::Stop),
            ITEM_SCREEN_RESUME => native_screen_control(crate::screen_control::Action::ResumeQuiet),
            ITEM_SCREEN_END => native_screen_control(crate::screen_control::Action::Hangup),
            ITEM_WATCH_START => native_watch_control("watch the computer"),
            ITEM_WATCH_CONFIRM => native_watch_control("confirm visual watch"),
            ITEM_WATCH_STOP => native_watch_control("stop watching"),
            ITEM_WATCH_STATUS => native_watch_control("watch mode status"),
            ITEM_WATCH_BRIEFING => native_watch_control("watch briefing"),
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

fn native_screen_control(action: crate::screen_control::Action) {
    // Native OS menu event only. Do not export as webview IPC or a CLI verb.
    std::thread::spawn(move || match crate::screen_control::send(action) {
        Ok(note) => desktop_notify("Jarvis screen voice", note),
        Err(note) => desktop_notify("Jarvis screen control", &note),
    });
}

fn native_watch_control(text: &'static str) {
    // Physical tray menu only; no model/webview command can confirm disclosure.
    std::thread::spawn(move || {
        let result = crate::health::http_post_json_on(
            &crate::env_cfg::bridge_host(),
            std::env::var("JARVIS_BRIDGE_PORT").ok().and_then(|s| s.parse().ok()).unwrap_or(crate::env_cfg::BRIDGE_PORT_DEFAULT),
            "/watch-control",
            &serde_json::json!({"text": text, "session_id": format!("native-tray-{}", std::process::id())}),
            crate::env_cfg::bridge_token().as_deref(),
        );
        let note = match result {
            Ok(value) => value.get("say").or_else(|| value.get("error")).and_then(|v| v.as_str()).unwrap_or("Watch Mode status unavailable.").to_string(),
            Err(_) => "Watch Mode bridge is unavailable.".to_string(),
        };
        if text == "watch briefing" || text == "watch the computer" {
            // Explicit owner click opens an ordinary dismissible native dialog;
            // private derived text never enters notifications or model context.
            let title = if text == "watch briefing" { "Jarvis Watch Mode briefing" } else { "Jarvis Watch Mode screenshot disclosure" };
            if std::process::Command::new("kdialog").args(["--title", title, "--msgbox", &note]).status().is_err() {
                desktop_notify("Jarvis Watch Mode", "The private briefing window could not open. Read the briefing through the authenticated owner controls.");
            }
        } else {
            desktop_notify("Jarvis Watch Mode", &note);
        }
    });
}

/// Show + focus the overlay window. No-op when it is already visible.
/// Startup restores/migrates geometry; summoning must not queue a second
/// resize from GTK's not-yet-mapped outer-size cache.
/// Center only the first summon with no saved placement. (0,0) is a valid
/// user-chosen position, and failed persistence must not recenter later summons.
static INITIAL_PLACEMENT: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
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
        let path = crate::overlay::geometry_path(&crate::env_cfg::jarvis_home());
        let first_show = !INITIAL_PLACEMENT.swap(true, std::sync::atomic::Ordering::SeqCst);
        let never_placed = first_show && crate::overlay::load_geometry(&path).is_none();
        let _ = win.show();
        if never_placed {
            let _ = win.center();
        }
        let _ = win.set_focus();
        // The window exists now: apply any click-through the HUD asked for
        // while it was hidden (see commands::set_overlay_click_through).
        crate::commands::apply_click_through(app);
        // Presence can dissolve the HUD without hiding the native window.
        // A repeated show must wake that HUD even without visibilitychange.
        let _ = win.eval("window.dispatchEvent(new Event('jarvis-hud-show'))");
    }
}

/// Run the HUD's animated return-to-wallpaper transition immediately.
pub fn fade_overlay(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("overlay") {
        if win.is_visible().unwrap_or(false) {
            let _ = win.eval("window.__jarvisPresence?.fade()");
        }
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
                // A hotkey/tray summon should wake a dissolved HUD first.
                // Keep the usual toggle-to-hide behaviour while present.
                let target = win.clone();
                if win.eval_with_callback(
                    "(() => { const p = window.__jarvisPresence; if (p && (p.state === 'gone' || p.state === 'fading' || p.state === 'appearing')) { window.dispatchEvent(new Event('jarvis-hud-show')); return true; } return false; })()",
                    move |value| {
                        if value == "true" {
                            let _ = target.set_focus();
                        } else {
                            let _ = target.hide();
                        }
                    },
                ).is_err() {
                    let _ = win.hide();
                }
            }
            _ => show_overlay(app),
        }
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
/// show-only, like the overlay: asking twice never hides it).
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

/// Show + focus the chess board (`jarvis chess`, started by the `chess`
/// command or by "Jarvis, let's play chess").
pub fn show_chess(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("chess") {
        let _ = win.unminimize();
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// Hide the chess board window.
pub fn hide_chess(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("chess") {
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
