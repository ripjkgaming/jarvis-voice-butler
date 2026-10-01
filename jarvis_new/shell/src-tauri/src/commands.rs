//! Frontend invoke commands for the static-export HUD (Phase 2 contract).
//!
//! The Next.js `/api/token` route does not exist in a static export, so the
//! webview calls these instead when `__TAURI_INTERNALS__` is present:
//!   - `app_config()` → `{ agentName }` (from shell env, same default chain
//!     as the worker: `AGENT_NAME` else `my-agent`).
//!   - `mint_token(room_config?)` → `{ serverUrl, roomName,
//!     participantName, participantToken }`, minted by exec'ing
//!     `src/mint_token.py` with a venv python (agent venv first, wake venv
//!     fallback). Secrets never touch the webview bundle: the script reads
//!     env / `.env.local` / `frontend/.env.local` itself.
//!   - `hide_overlay()` → hides the overlay window (HUD Esc key; the shell
//!     has no other way to hear webview key events).
//!   - `set_overlay_click_through(ignore)` → forwards mouse events through
//!     the overlay when the orb is idle so it never blocks work beneath;
//!     the HUD turns it back off while interacting (see hud-shell).

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager};

use crate::env_cfg::{self, AGENT_NAME_DEFAULT};
use crate::health;

/// Window label of the overlay (must match `tauri.conf.json`; frozen).
pub const OVERLAY_LABEL: &str = "overlay";

/// Shell-side memory of the desired mic state. The tray checkbox, the HUD
/// mic button, and `jarvis mute|unmute` all write it; the shell re-asserts
/// it on the wake listener whenever the stack reports Ready (sidecar
/// restarts lose the in-process mute otherwise).
pub struct MicDesired(pub Mutex<bool>);

impl Default for MicDesired {
    fn default() -> Self {
        Self(Mutex::new(false))
    }
}

/// Bridge port honoring `JARVIS_BRIDGE_PORT` (same default as the bridge).
fn bridge_port() -> u16 {
    std::env::var("JARVIS_BRIDGE_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(env_cfg::BRIDGE_PORT_DEFAULT)
}

/// Bridge Bearer token — see [`env_cfg::bridge_token`]. Thin local alias
/// so mute-path call sites stay short.
fn bridge_token() -> Option<String> {
    env_cfg::bridge_token()
}

/// Host the bridge serves (tailnet IP for phone setups, else loopback).
/// Every bridge call must go here, never hardcoded 127.0.0.1.
fn bridge_host() -> String {
    env_cfg::bridge_host()
}

/// Blocking core of the mute path: POST `/mic` to the bridge (which proxies
/// to wake.sock with a 2s timeout). Shared by the async invoke command, the
/// tray checkbox, and the single-instance CLI verbs. Returns the mute the
/// wake listener confirmed.
pub fn post_mic_muted(muted: bool) -> Result<bool, String> {
    let reply = health::http_post_json_on(
        &bridge_host(),
        bridge_port(),
        "/mic",
        &serde_json::json!({"muted": muted}),
        bridge_token().as_deref(),
    )
    .map_err(|e| format!("mic mute unreachable: {e}"))?;
    if reply.get("ok").and_then(|v| v.as_bool()) != Some(true) {
        let detail = reply
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("wake listener refused");
        return Err(format!("mic mute failed: {detail}"));
    }
    reply
        .get("muted")
        .and_then(|v| v.as_bool())
        .ok_or_else(|| "mic mute: bad bridge reply".to_string())
}

/// Blocking core of the status path: GET `/mic` from the bridge.
pub fn get_mic_status() -> Result<serde_json::Value, String> {
    let reply = health::http_get_json_authed_on(
        &bridge_host(),
        bridge_port(),
        "/mic",
        bridge_token().as_deref(),
    )
    .map_err(|e| format!("mic status unreachable: {e}"))?;
    if reply.get("ok").and_then(|v| v.as_bool()) != Some(true) {
        let detail = reply
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("wake listener unavailable");
        return Err(format!("mic status: {detail}"));
    }
    Ok(reply)
}

/// Remember the desired mute in shell state (tray checkbox stays truthful
/// across sidecar restarts; re-assert reads it back).
pub fn set_desired_mute(app: &AppHandle, muted: bool) {
    if let Some(state) = app.try_state::<MicDesired>() {
        if let Ok(mut guard) = state.0.lock() {
            *guard = muted;
        }
    }
}

/// Desired mute remembered by the shell (default: unmuted).
pub fn desired_mic_muted(app: &AppHandle) -> bool {
    app.try_state::<MicDesired>()
        .and_then(|s| s.0.lock().ok().map(|g| *g))
        .unwrap_or(false)
}

/// HUD/tray mic button: set the mute, remember it, mirror it on the tray
/// checkbox. Returns the mute the wake listener confirmed. Blocking bridge
/// I/O runs on the blocking pool so the webview never stalls the runtime.
#[tauri::command]
pub async fn set_mic_muted(app: AppHandle, muted: bool) -> Result<bool, String> {
    let confirmed = tauri::async_runtime::spawn_blocking(move || post_mic_muted(muted))
        .await
        .map_err(|e| format!("mic mute task: {e}"))??;
    set_desired_mute(&app, confirmed);
    crate::tray::set_mute_checked(&app, confirmed);
    Ok(confirmed)
}

/// HUD mic button state query: `{muted, threshold, in_call}` or Err when
/// the wake listener/bridge is unreachable (fail-soft at the caller).
#[tauri::command]
pub async fn mic_status() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(get_mic_status)
        .await
        .map_err(|e| format!("mic status task: {e}"))?
}

/// PTT summon from the HUD (NumpadEnter): show the overlay, then ask the
/// wake listener for a talk session via bridge POST /summon — the same
/// summon the "hey Jarvis" hotword performs, without the hotword.
/// Blocking bridge I/O runs on the blocking pool. When wake auto-unmutes
/// for the call, mirror that on shell state + tray checkbox so nothing
/// lies. Err when the chain is unreachable (fail-soft at the caller).
#[tauri::command]
pub async fn talk(app: AppHandle) -> Result<serde_json::Value, String> {
    crate::tray::show_overlay(&app);
    let reply = tauri::async_runtime::spawn_blocking(|| {
        health::http_post_json_on(
            &bridge_host(),
            bridge_port(),
            "/summon",
            &serde_json::json!({}),
            bridge_token().as_deref(),
        )
        .map_err(|e| format!("summon unreachable: {e}"))
    })
    .await
    .map_err(|e| format!("summon task: {e}"))??;
    if reply.get("ok").and_then(|v| v.as_bool()) != Some(true) {
        let detail = reply
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("wake listener refused");
        return Err(format!("summon failed: {detail}"));
    }
    if reply.get("muted").and_then(|v| v.as_bool()) == Some(false) {
        set_desired_mute(&app, false);
        crate::tray::set_mute_checked(&app, false);
    }
    Ok(reply)
}

/// Blocking diagnostics runner: agent-venv `src/diagnostics.py` (stdlib,
/// redacted bundle). Returns the tarball path parsed from the script's
/// last stdout line. 3-minute hard timeout; never panics.
pub fn run_diagnostics_blocking() -> Result<PathBuf, String> {
    let repo = env_cfg::repo_root();
    let progs = env_cfg::sidecar_programs(&repo);
    let script = repo.join("src").join("diagnostics.py");
    if !script.is_file() {
        return Err(format!("diagnostics.py missing at {}", script.display()));
    }
    let (tx, rx) = std::sync::mpsc::channel();
    let py = progs.agent_python.clone();
    let env = env_cfg::sidecar_env(&repo);
    std::thread::spawn(move || {
        let out = std::process::Command::new(&py)
            .arg(&script)
            .envs(&env)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .output();
        let _ = tx.send(out);
    });
    let out = match rx.recv_timeout(Duration::from_secs(180)) {
        Err(_) => return Err("diagnostics timed out after 180s".to_string()),
        Ok(Err(e)) => return Err(format!("diagnostics spawn failed: {e}")),
        Ok(Ok(out)) => out,
    };
    if !out.status.success() {
        let detail = String::from_utf8_lossy(&out.stderr);
        let tail = detail.lines().last().unwrap_or("unknown error");
        return Err(format!("diagnostics failed: {}", tail.trim()));
    }
    let text = String::from_utf8_lossy(&out.stdout);
    text.lines()
        .last()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .ok_or_else(|| "diagnostics printed no path".to_string())
}

/// Bridge connection surface for the HUD: `{ url, token }`.
///
/// The bridge may bind the tailnet IP (phone access) instead of loopback,
/// so the HUD cannot assume 127.0.0.1. The token (when set) goes into the
/// `Authorization: Bearer` header the bridge demands. Same-machine trust:
/// the webview can already invoke `mint_token`, so handing it the loopback
/// credential adds no privilege.
#[tauri::command]
pub fn bridge_info() -> serde_json::Value {
    let host = env_cfg::bridge_host();
    let token = env_cfg::bridge_token();
    serde_json::json!({ "url": format!("http://{host}:{}", bridge_port()), "token": token })
}

#[tauri::command]
pub fn app_config() -> serde_json::Value {
    let agent_name = std::env::var("AGENT_NAME")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| AGENT_NAME_DEFAULT.to_string());
    serde_json::json!({ "agentName": agent_name })
}

/// Shell-side twin of `mint_token.valid_room_name`: wake room names are
/// `jarvis-<epoch>`. Anything else is rejected (the script would mint a
/// stray summon-mode room instead — never what a join call wants). Pure.
pub fn sanitize_room_name(name: &str) -> Option<String> {
    let name = name.trim();
    if name.is_empty() || name.len() > 64 {
        return None;
    }
    if name
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        Some(name.to_string())
    } else {
        None
    }
}

#[tauri::command]
pub async fn mint_token(room_name: Option<String>) -> Result<serde_json::Value, String> {
    // Join mode: an explicit wake room → token WITHOUT dispatch (the agent
    // is already there; dispatching again would summon a second voice).
    // None/invalid → legacy summon mode (fresh room + dispatch).
    let join_room: Option<String> = room_name.as_deref().and_then(sanitize_room_name);
    let repo = env_cfg::repo_root();
    let progs = env_cfg::sidecar_programs(&repo);
    let script = repo.join("src").join("mint_token.py");
    if !script.is_file() {
        return Err(format!("mint_token.py missing at {}", script.display()));
    }
    let candidates: Vec<PathBuf> = vec![
        progs.agent_python,
        progs.wake_python,
        PathBuf::from("python3"),
    ];
    let mut last_err = "no python interpreter found".to_string();
    for py in &candidates {
        let mut cmd = tokio::process::Command::new(py);
        cmd.arg(&script);
        if let Some(room) = &join_room {
            // mint_token.py [agent] [room] join — no dispatch (see script).
            let agent = std::env::var("AGENT_NAME")
                .ok()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| AGENT_NAME_DEFAULT.to_string());
            cmd.arg(agent).arg(room).arg("join");
        }
        cmd.envs(env_cfg::sidecar_env(&repo))
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        let out = match tokio::time::timeout(Duration::from_secs(15), cmd.output()).await {
            Err(_) => {
                last_err = "mint_token timed out".to_string();
                break;
            }
            Ok(Err(e)) => {
                last_err = format!("mint_token spawn failed: {e}");
                continue; // e.g. NotFound: try the next interpreter
            }
            Ok(Ok(out)) => out,
        };
        if !out.status.success() {
            let detail = String::from_utf8_lossy(&out.stderr);
            last_err = format!("mint_token failed: {}", detail.trim());
            break; // script ran and said no: retrying won't help
        }
        let value: serde_json::Value =
            serde_json::from_slice(&out.stdout).map_err(|e| format!("mint_token bad json: {e}"))?;
        if let Some(err) = value.get("error").and_then(|e| e.as_str()) {
            last_err = format!("mint_token: {err}");
            break;
        }
        return Ok(value);
    }
    Err(last_err)
}

/// Hide the overlay window. Called by the HUD on Esc (blur-hide is handled
/// shell-side via `WindowEvent::Focused`). Missing window = no-op success.
#[tauri::command]
pub fn hide_overlay(app: AppHandle) -> Result<(), String> {
    match app.get_webview_window(OVERLAY_LABEL) {
        Some(win) => win.hide().map_err(|e| format!("hide overlay: {e}")),
        None => Ok(()),
    }
}

/// Forward (`true`) or accept (`false`) mouse events on the overlay.
/// The HUD sets `true` when the orb is idle and no field is focused, so the
/// always-on-top window never blocks clicks to windows beneath; any
/// interaction flips it back off. Missing window = no-op success.
///
/// GTK hazard (crashed the 01:00 boot): `set_ignore_cursor_events` on a
/// hidden/unrealized window unwraps a None GDK window inside tao and
/// panics the process. So the request is forwarded ONLY while visible;
/// otherwise the desire is stashed and `apply_click_through` (called from
/// every show path) applies it once the window exists.
pub fn click_through_desired() -> bool {
    CLICK_THROUGH_DESIRED.load(std::sync::atomic::Ordering::SeqCst)
}

/// `set_ignore_cursor_events` only while the window is visible (see the
/// GTK hazard above); a hidden window is left alone. Never panics.
pub fn click_through_if_visible(win: &tauri::WebviewWindow, ignore: bool) {
    if win.is_visible().unwrap_or(false) {
        let _ = win.set_ignore_cursor_events(ignore);
    }
}

/// Apply the stashed click-through desire to a now-visible window.
pub fn apply_click_through(app: &AppHandle) {
    if let Some(win) = app.get_webview_window(OVERLAY_LABEL) {
        if win.is_visible().unwrap_or(false) {
            let _ = win.set_ignore_cursor_events(click_through_desired());
        }
    }
}

static CLICK_THROUGH_DESIRED: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

#[tauri::command]
pub fn set_overlay_click_through(app: AppHandle, ignore: bool) -> Result<(), String> {
    CLICK_THROUGH_DESIRED.store(ignore, std::sync::atomic::Ordering::SeqCst);
    match app.get_webview_window(OVERLAY_LABEL) {
        Some(win) => {
            if win.is_visible().unwrap_or(false) {
                win.set_ignore_cursor_events(ignore)
                    .map_err(|e| format!("click-through: {e}"))?;
            }
            Ok(())
        }
        None => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_config_always_names_an_agent() {
        let v = app_config();
        let name = v.get("agentName").and_then(|n| n.as_str()).unwrap();
        assert!(!name.is_empty());
    }

    #[test]
    fn bridge_info_points_at_a_loopback_default() {
        let vars = ["JARVIS_BRIDGE_BIND", "JARVIS_BRIDGE_TOKEN"];
        let saved: Vec<(String, Option<String>)> = vars
            .iter()
            .map(|v| (v.to_string(), std::env::var(v).ok()))
            .collect();
        std::env::remove_var("JARVIS_BRIDGE_BIND");
        std::env::remove_var("JARVIS_BRIDGE_TOKEN");
        let v = bridge_info();
        let url = v.get("url").and_then(|u| u.as_str()).unwrap();
        assert!(url.starts_with("http://127.0.0.1:"), "{url}");
        assert!(v.get("token").is_some());
        for (v, old) in saved {
            match old {
                Some(val) => std::env::set_var(&v, val),
                None => std::env::remove_var(&v),
            }
        }
    }

    #[test]
    fn room_names_are_strictly_allowlisted() {
        assert_eq!(
            sanitize_room_name("jarvis-123"),
            Some("jarvis-123".to_string())
        );
        assert_eq!(
            sanitize_room_name("  jarvis-1  "),
            Some("jarvis-1".to_string())
        );
        assert_eq!(sanitize_room_name(""), None);
        assert_eq!(sanitize_room_name("../../etc"), None);
        assert_eq!(sanitize_room_name("a b"), None);
        assert_eq!(sanitize_room_name(&"x".repeat(65)), None);
    }

    #[test]
    fn diagnostics_fails_soft_without_the_script() {
        // Fake but valid-looking repo root without diagnostics.py: the
        // runner must Err, never panic or hang.
        let tmp = std::env::temp_dir().join("jarvis-diag-test");
        std::fs::create_dir_all(tmp.join("src")).ok();
        std::fs::write(tmp.join("src").join("agent.py"), "# fake").ok();
        std::fs::write(tmp.join("pyproject.toml"), "# fake").ok();
        std::env::set_var("JARVIS_REPO", &tmp);
        let err = run_diagnostics_blocking().expect_err("script is missing");
        assert!(err.contains("diagnostics.py missing"), "{err}");
        std::env::remove_var("JARVIS_REPO");
        std::fs::remove_dir_all(&tmp).ok();
    }
}
