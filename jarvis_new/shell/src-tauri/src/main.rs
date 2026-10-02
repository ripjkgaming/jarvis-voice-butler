//! Jarvis desktop shell: tray + overlay + sidecar supervision.
//!
//! The shell owns what the Python sidecars cannot do themselves: boot order
//! with readiness gates ([`manager`]), the contract env ([`env_cfg`]),
//! tray presence ([`tray`]), global summon ([`hotkey`]), single-instance
//! CLI (`toggle|talk|suitshow|mute|unmute|quit|diagnostics`), and autostart.
//! Product logic stays in Python/TS.

mod commands;
mod env_cfg;
mod health;
mod hotkey;
mod manager;
mod overlay;
mod orb;
mod school;
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
                "globe" => tray::toggle_globe(app),
                "globeshow" => tray::show_globe(app),
                "globestate" => print_window_state(app, "globe"),
                "projects" => tray::toggle_projects(app),
                "projectsshow" => tray::show_projects(app),
                "projectshide" => tray::hide_projects(app),
                "projectsstate" => print_window_state(app, "projects"),
                "draftsshow" => tray::show_drafts(app),
                "draftshide" => tray::hide_drafts(app),
                "draftsstate" => print_window_state(app, "drafts"),
                "suitshow" => tray::show_suit_overlay(app),
                "state" => print_overlay_state(app),
                "schoolon" => school::enter(app),
                "schooloff" => school::exit(app),
                "schoolmenu" => school::open_menu(app, args.get(2).map(String::as_str).unwrap_or("launcher")),
                "orbon" => orb::enter(app),
                "orboff" => orb::exit(app),
                "domstate" => print_dom_state(app),
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
            commands::bridge_info,
            commands::mint_token,
            commands::hide_overlay,
            commands::set_overlay_click_through,
            commands::set_mic_muted,
            commands::mic_status,
            commands::talk,
            school::school_menu,
            school::school_stage,
            school::suit_focus
        ])
        // No `capabilities/` dir in Phase 1: the placeholder UI makes zero
        // frontend→backend calls, and all plugin use below is Rust-side
        // (tray, hotkey, autostart need no IPC grants). Phase 2 adds
        // `invoke('mint_token')` + capabilities together.
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .setup(move |app| {
            // `suitshow` only targets an existing primary. The single-instance
            // plugin forwards it and exits before this setup when one exists.
            // If that instance disappeared, fail before starting any sidecars.
            if std::env::args().nth(1).as_deref() == Some("suitshow") {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::NotConnected,
                    "suitshow requires the running Jarvis desktop shell",
                )
                .into());
            }
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
            // School mode survives restarts: come back as the strip.
            if school::school_on_disk(&env_cfg::jarvis_home()) {
                school::enter_quiet(app.handle());
            } else {
                school::fill_work_area_at_boot();
            }

            // Cold-start verb: the single-instance callback only fires for
            // a SECOND launch, so a first launch like `jarvis talk` (used
            // by the KWin Meta+J shortcut, which must work when the app is
            // not running yet) would otherwise start silently to the tray.
            // Mirror the `talk` arm here: show the window now, emit TALK
            // after a short delay so the frontend has mounted its listener.
            if std::env::args().nth(1).as_deref() == Some("talk") {
                tray::show_overlay(app.handle());
                let talk_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    tokio::time::sleep(std::time::Duration::from_secs(4)).await;
                    let _ = talk_handle.emit(tray::EVENT_TALK, ());
                });
            }

            // Bare launch (login autostart, app menu): the overlay is
            // `visible: false` in tauri.conf.json, so without this the HUD
            // only lived in the tray until summoned. School mode already
            // re-docked the strip above, so leave that alone.
            if std::env::args().nth(1).is_none() && !school::is_school() {
                tray::show_overlay(app.handle());
            }

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

/// `jarvis state`: report overlay visibility + geometry on stdout for
/// scripting and diagnostics. Runs in the second (requesting) process via
/// the single-instance callback — prints against the PRIMARY window.
fn print_overlay_state(app: &tauri::AppHandle) {
    print_window_state(app, commands::OVERLAY_LABEL);
}

/// `jarvis globestate`: same visibility report for the globe window.
fn print_window_state(app: &tauri::AppHandle, label: &str) {
    // stderr: unbuffered past the primary's redirected log (stdout would
    // sit in the block buffer until exit).
    match app.get_webview_window(label) {
        None => eprintln!("{label}: missing"),
        Some(win) => {
            let visible = win.is_visible().unwrap_or(false);
            let minimized = win.is_minimized().unwrap_or(false);
            let focused = win.is_focused().unwrap_or(false);
            let pos = win.outer_position().map(|p| format!("{},{}", p.x, p.y));
            let size = win
                .outer_size()
                .map(|s| format!("{}x{}", s.width, s.height));
            eprintln!(
                "{label}: visible={visible} minimized={minimized} focused={focused} pos={} size={}",
                pos.unwrap_or_else(|_| "?".to_string()),
                size.unwrap_or_else(|_| "?".to_string())
            );
        }
    }
}
fn print_dom_state(app: &tauri::AppHandle) {
    // stderr (unbuffered). Asks the live webview what it actually
    // rendered — settles "blank page" vs "unmapped window" for good.
    match app.get_webview_window(commands::OVERLAY_LABEL) {
        None => eprintln!("domstate: missing"),
        Some(win) => {
            let (tx, rx) = std::sync::mpsc::channel();
            let js = "(() => { try { const h = document.querySelector('.hud__header'); const l = document.querySelector('.hud-linking'); return JSON.stringify({url: location.href, title: document.title, header: h ? h.innerText.slice(0, 80) : null, pill: l ? l.innerText.slice(0, 80) : null, vw: innerWidth, vh: innerHeight, dpr: devicePixelRatio, orb: !!document.querySelector('.hud-orb-dot'), hidden: document.hidden, sbar: (() => { const b = document.querySelector('.sbar'); if (!b) return null; const r = (e) => { const q = e.getBoundingClientRect(); return [Math.round(q.top), Math.round(q.height), getComputedStyle(e).transform]; }; return {bar: r(b), zones: [...b.querySelectorAll(':scope > section')].map(r), body: r(document.body), parents: (() => { const out = []; let e = b.parentElement; while (e && out.length < 4) { out.push(e.tagName + '.' + e.className.toString().slice(0, 30) + ':' + r(e).slice(0, 2).join('/')); e = e.parentElement; } return out; })()}; })()}); } catch (e) { return 'ERR:' + e; } })()";
            match win.eval_with_callback(js, move |v| {
                let _ = tx.send(v);
            }) {
                Err(e) => eprintln!("domstate: eval failed ({e})"),
                Ok(()) => match rx.recv_timeout(std::time::Duration::from_secs(5)) {
                    Ok(v) => eprintln!("domstate: {v}"),
                    Err(_) => eprintln!("domstate: timeout waiting for webview"),
                },
            }
        }
    }
}

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
/// Pre-16:9 portrait geometries migrate to 1280x720 in place (position kept).
fn restore_overlay_geometry(app: &tauri::AppHandle) {
    let path = overlay::geometry_path(&env_cfg::jarvis_home());
    let mut geom = match overlay::load_geometry(&path) {
        Some(g) => g,
        None => return,
    };
    if overlay::needs_migration(&geom) {
        geom.width = overlay::OVERLAY_WIDTH;
        geom.height = overlay::OVERLAY_HEIGHT;
        // Persist the migration so the next boot is already correct.
        // Fail-soft: a failed save must never hide the window.
        if let Err(e) = overlay::save_geometry(&path, &geom) {
            eprintln!("jarvis: overlay geometry migrate-save failed ({e})");
        }
    }
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

/// Overlay window events: move/resize persist geometry for the next boot
/// so the window behaves like a normal movable window. No blur-hide: the
/// window stays where put until hidden via the header ×, Esc, Super+J, the
/// tray, or `jarvis toggle`. Everything fail-soft.
fn on_overlay_window_event(app: &tauri::AppHandle, event: tauri::WindowEvent) {
    match event {
        // [X] / Alt+F4 / taskbar-close must HIDE, never destroy: a closed
        // window keeps reporting visible=true while compositing nothing,
        // which reads exactly like "the overlay vanished". Super+J / tray
        // / `jarvis talk` bring it back.
        tauri::WindowEvent::CloseRequested { api, .. } => {
            api.prevent_close();
            if let Some(win) = app.get_webview_window(commands::OVERLAY_LABEL) {
                let _ = win.hide();
            }
        }
        tauri::WindowEvent::Moved(_) | tauri::WindowEvent::Resized(_) => {
            // The strip's / orb's size must never overwrite the saved HUD.
            if !school::is_school() && !orb::is_orb() {
                persist_overlay_geometry(app);
            }
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
