//! Jarvis desktop shell: tray + overlay + sidecar supervision.
//!
//! The shell owns what the Python sidecars cannot do themselves: boot order
//! with readiness gates ([`manager`]), the contract env ([`env_cfg`]),
//! tray presence ([`tray`]), global summon ([`hotkey`]), single-instance
//! CLI (`toggle|talk|quit`), and autostart. Product logic stays in Python/TS.

mod commands;
mod env_cfg;
mod health;
mod hotkey;
mod manager;
mod tray;

use std::path::PathBuf;

use tauri::{Emitter, Listener};
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
                _ => tray::toggle_overlay(app),
            }
        }))
        .plugin(tauri_plugin_cli::init())
        .invoke_handler(tauri::generate_handler![
            commands::app_config,
            commands::mint_token
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
            match hotkey::register(app.handle()) {
                Ok(accel) => println!("jarvis: hotkey {accel} armed"),
                Err(e) => eprintln!("jarvis: hotkey unavailable ({e}); use tray or KWin shortcut"),
            }

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
                    tray::update_status(&handle, &rx.borrow().clone());
                }
            });

            // Supervise sidecars in the background.
            let repo = env_cfg::repo_root();
            let progs = env_cfg::sidecar_programs(&repo);
            for m in &progs.missing {
                eprintln!("jarvis: missing dependency: {m}");
            }
            let specs = manager::build_specs(
                &progs,
                env_cfg::LIVEKIT_PORT,
                std::env::var("JARVIS_BRIDGE_PORT")
                    .ok()
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(env_cfg::BRIDGE_PORT_DEFAULT),
            );
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
    app.run(move |handle, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            let _ = shutdown_on_exit.send(true);
            let _ = handle;
        }
    });
}
