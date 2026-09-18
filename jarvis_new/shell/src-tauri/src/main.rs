//! Jarvis desktop shell: tray + overlay + sidecar supervision.
//!
//! The shell owns what the Python sidecars cannot do themselves: boot order
//! with readiness gates ([`manager`]), the contract env ([`env_cfg`]),
//! tray presence ([`tray`]), global summon ([`hotkey`]), single-instance
//! CLI (`toggle|talk|mute|unmute|quit|diagnostics`), and autostart.
//! Product logic stays in Python/TS.

mod commands;
mod env_cfg;
mod health;
mod hotkey;
mod manager;
mod overlay;
mod tray;
mod updater;

use std::path::PathBuf;

use tauri::{Emitter, Listener, Manager};
use tauri_plugin_autostart::ManagerExt;

use crate::health::ShellState;

fn log_dir() -> PathBuf {
    let dir = env_cfg::jarvis_home().join("logs");
    std::fs::create_dir_all(&dir).ok();
    dir
}

fn main() {
    let (state_tx, state_rx) = tokio::sync::watch::channel(ShellState::Starting);
    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            // Second launch: route CLI verbs into the running instance.
            // `jarvis toggle` shows/hides the overlay, `jarvis talk`
            // summons, `jarvis quit` exits.
            let verb = args.get(1).map(String::as_str).unwrap_or("toggle");
            match verb {
                "quit" => app.exit(0),
                "talk" => {
                    tray::show_overlay(app);
                    let _ = app.emit(tray::EVENT_TALK, ());
                }
                "mute" => set_mute_from_cli(app, true),
                "unmute" => set_mute_from_cli(app, false),
                "diagnostics" => run_diagnostics_from_cli(),
                _ => tray::toggle_overlay(app),
            }
        }))
        .plugin(tauri_plugin_cli::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .invoke_handler(tauri::generate_handler![
            commands::app_config,
            commands::mint_token,
            commands::hide_overlay,
            commands::set_overlay_click_through,
            commands::set_mic_muted,
            commands::mic_status
        ])
        // No `capabilities/` dir in Phase 1: the placeholder UI makes zero
        // frontend→backend calls, and all plugin use below is Rust-side
        // (tray, hotkey, autostart need no IPC grants). Phase 2 adds
        // `invoke('mint_token')` + capabilities together.
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(move |app| {
            // Autostart default-ON with tray opt-out: enable once, then
            // leave the user's choice alone (marker file = asked already).
            let marker = env_cfg::jarvis_home().join(".autostart Asked");
            if !marker.is_file() {
                let _ = app.autolaunch().enable();
                std::fs::write(&marker, "1").ok();
            }

            tray::build_tray(app.handle())?;
            // Desired mic state survives sidecar restarts (re-asserted on
            // every transition to Running below).
            app.manage(commands::MicDesired::default());
            // Update-note state (v1: checks only, see updater.rs).
            app.manage(updater::UpdateNote::default());
            match hotkey::register(app.handle()) {
                Ok(accel) => println!("jarvis: hotkey {accel} armed"),
                Err(e) => eprintln!("jarvis: hotkey unavailable ({e}); use tray or KWin shortcut"),
            }

            // Overlay geometry: restore last position/size (fail-soft to
            // the tauri.conf.json defaults when nothing was ever saved).
            restore_overlay_geometry(app.handle());

            // v1 update check (checks only, never installs): one boot-time
            // pass, fail-soft. A staged newer version surfaces as a tray
            // note + notification; the placeholder feed just yields
            // "check unavailable" with no popup.
            let update_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let (note, notify) =
                    updater::note_for(&updater::check_for_update(&update_handle).await);
                updater::set_updates_note(&update_handle, &note);
                if notify {
                    tray::desktop_notify("Jarvis update available", &note);
                }
            });

            // Overlay event wiring.
            let handle = app.handle().clone();
            app.listen(tray::EVENT_TOGGLE, move |_| {
                tray::toggle_overlay(&handle);
            });

            // Tray mirrors manager state.
            let handle = app.handle().clone();
            let mut rx = state_rx.clone();
            tauri::async_runtime::spawn(async move {
                while rx.changed().await.is_ok() {
                    let state = rx.borrow().clone();
                    tray::update_status(&handle, &state);
                    // Sidecar (re)starts lose the in-process wake mute, so
                    // re-assert the desired state once everything is Ready.
                    // Best-effort and idempotent: a failed POST just means
                    // the listener isn't up yet; the next Ready retries.
                    if state == ShellState::Running && commands::desired_mic_muted(&handle) {
                        tauri::async_runtime::spawn_blocking(|| {
                            let _ = commands::post_mic_muted(true);
                        });
                    }
                }
            });

            // Shell owns dotenv: sidecars inherit this process env (see
            // env_cfg::sidecar_env) and the bridge reports correct flags.
            let repo = env_cfg::repo_root();
            let dotenv_loaded = env_cfg::load_dotenv_files(&repo);
            if dotenv_loaded > 0 {
                println!("jarvis: loaded {dotenv_loaded} dotenv file(s)");
            }
            let progs = env_cfg::sidecar_programs(&repo);
            for m in &progs.missing {
                eprintln!("jarvis: missing dependency: {m}");
            }
            // Reap leftovers from a shell that died without its kill-tree
            // (watcher rebuilds, SIGKILL, crashes) BEFORE spawning our set.
            let specs = manager::build_specs(
                &progs,
                env_cfg::LIVEKIT_PORT,
                std::env::var("JARVIS_BRIDGE_PORT")
                    .ok()
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(env_cfg::BRIDGE_PORT_DEFAULT),
            );
            let reaped = manager::reap_stale_sidecars(&specs);
            if !reaped.is_empty() {
                println!("jarvis: cleared {} stale sidecar(s) at boot", reaped.len());
            }
            let env = env_cfg::sidecar_env(&repo);
            let logs = log_dir();
            tauri::async_runtime::spawn(manager::supervise(
                specs,
                env,
                logs,
                state_tx,
                shutdown_rx,
            ));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("jarvis shell failed to build");

    let shutdown_on_exit = shutdown_tx.clone();
    app.run(move |handle, event| match event {
        tauri::RunEvent::ExitRequested { .. } => {
            let _ = shutdown_on_exit.send(true);
        }
        tauri::RunEvent::WindowEvent {
            label,
            event: win_event,
            ..
        } if label == commands::OVERLAY_LABEL => {
            on_overlay_window_event(handle, win_event);
        }
        _ => {}
    });
}

/// `jarvis mute|unmute` from a second CLI launch: remember the desire and
/// push it to the wake listener off the single-instance callback thread.
fn set_mute_from_cli(app: &tauri::AppHandle, muted: bool) {
    commands::set_desired_mute(app, muted);
    let handle = app.clone();
    std::thread::spawn(move || match commands::post_mic_muted(muted) {
        Ok(confirmed) => {
            commands::set_desired_mute(&handle, confirmed);
            tray::set_mute_checked(&handle, confirmed);
        }
        Err(e) => eprintln!("jarvis: cli mute failed ({e})"),
    });
}

/// `jarvis diagnostics`: collect the redacted bundle off the
/// single-instance callback thread, then surface the tarball path. The
/// requesting terminal is the *second* process and is already gone (the
/// plugin can't reply to it), so the path goes to a desktop notification,
/// the primary log, and `~/.jarvis/last-diagnostics` for scripting.
fn run_diagnostics_from_cli() {
    std::thread::spawn(|| match commands::run_diagnostics_blocking() {
        Ok(path) => {
            let marker = env_cfg::jarvis_home().join("last-diagnostics");
            let _ = std::fs::write(&marker, path.to_string_lossy().as_bytes());
            println!("jarvis: diagnostics ready at {}", path.display());
            tray::desktop_notify("Jarvis diagnostics ready", &path.to_string_lossy());
        }
        Err(e) => eprintln!("jarvis: diagnostics failed ({e})"),
    });
}

/// Restore the persisted overlay geometry (Phase 4.1). No-op when nothing
/// was saved yet or the window is missing; never fails the boot.
fn restore_overlay_geometry(app: &tauri::AppHandle) {
    let path = overlay::geometry_path(&env_cfg::jarvis_home());
    let geom = match overlay::load_geometry(&path) {
        Some(g) => g,
        None => return,
    };
    if let Some(win) = app.get_webview_window(commands::OVERLAY_LABEL) {
        let _ = win.set_position(tauri::Position::Physical(tauri::PhysicalPosition {
            x: geom.x,
            y: geom.y,
        }));
        let _ = win.set_size(tauri::Size::Physical(tauri::PhysicalSize {
            width: geom.width,
            height: geom.height,
        }));
    }
}

/// Overlay window events: focus-out hides (blur-hide), move/resize persist
/// geometry for the next boot. Everything fail-soft.
fn on_overlay_window_event(app: &tauri::AppHandle, event: tauri::WindowEvent) {
    match event {
        tauri::WindowEvent::Focused(false) => {
            if let Some(win) = app.get_webview_window(commands::OVERLAY_LABEL) {
                let _ = win.hide();
            }
        }
        tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_) => {
            persist_overlay_geometry(app);
        }
        _ => {}
    }
}

/// Read the live overlay position/size and save it. Physical pixels, same
/// units `restore_overlay_geometry` feeds back into `set_position`/`set_size`.
fn persist_overlay_geometry(app: &tauri::AppHandle) {
    let win = match app.get_webview_window(commands::OVERLAY_LABEL) {
        Some(w) => w,
        None => return,
    };
    let (Ok(pos), Ok(size)) = (win.outer_position(), win.outer_size()) else {
        return;
    };
    let geom = overlay::OverlayGeometry {
        x: pos.x,
        y: pos.y,
        width: size.width,
        height: size.height,
    };
    let path = overlay::geometry_path(&env_cfg::jarvis_home());
    if let Err(e) = overlay::save_geometry(&path, &geom) {
        eprintln!("jarvis: overlay geometry save failed ({e})");
    }
}
