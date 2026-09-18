//! Updater v1: version checks only. No auto-install, no signing yet.
//!
//! The `tauri-plugin-updater` dependency + `tauri.conf.json` section exist
//! so post-v1 can add verified installs, but NOTHING in v1 downloads,
//! verifies, or installs anything: the only runtime call is
//! `Updater::check()`, which fetches the feed JSON and compares versions.
//! The configured pubkey is an obvious placeholder (`UNSET-…`) — signature
//! verification never runs because the download/install path is never
//! invoked (guarded by the `no_auto_install_in_v1` test below). Post-v1:
//! generate a release keypair, keep the secret offline, put the real
//! public key in `tauri.conf.json`, then wire download + install.
//!
//! Feed: `JARVIS_UPDATE_FEED` overrides the placeholder endpoint for
//! staging (the plugin reads static config, so staging swaps the whole
//! config or env at package time — v1 has no real feed). A staged newer
//! version surfaces as a tray note + desktop notification.

use std::sync::Mutex;

use tauri::{AppHandle, Manager};

/// Placeholder feed (must match `tauri.conf.json`): unroutable by design.
/// Real feeds arrive post-v1 with signing. Referenced by the wiring test;
/// the plugin itself reads the URL from `tauri.conf.json` at runtime.
#[allow(dead_code)]
pub const FEED_PLACEHOLDER: &str = "https://example.invalid/jarvis/updates.json";

/// Tray note state ("Updates: up to date" / "Updates: 0.2.0 available").
pub struct UpdateNote(pub Mutex<String>);

impl Default for UpdateNote {
    fn default() -> Self {
        Self(Mutex::new("Updates: not checked".to_string()))
    }
}

/// Parse the leading numeric `major.minor.patch` of a version string.
/// Non-numeric suffixes (`-alpha`, `+build`) are ignored. Pure.
fn numeric_parts(version: &str) -> Vec<u64> {
    version
        .split(|c: char| !(c.is_ascii_digit() || c == '.'))
        .next()
        .unwrap_or("")
        .split('.')
        .filter_map(|p| p.parse().ok())
        .collect()
}

/// True when `latest` is strictly newer than `current`. Pure.
pub fn is_newer(current: &str, latest: &str) -> bool {
    let (a, b) = (numeric_parts(current), numeric_parts(latest));
    for i in 0..a.len().max(b.len()) {
        let (x, y) = (*a.get(i).unwrap_or(&0), *b.get(i).unwrap_or(&0));
        if x != y {
            return y > x;
        }
    }
    false
}

/// Tray/note text when `latest` is newer, else `None`. Pure.
pub fn update_note(current: &str, latest: &str) -> Option<String> {
    if is_newer(current, latest) {
        Some(format!("Updates: {latest} available (current {current})"))
    } else {
        None
    }
}

/// Mirror a note on the tray item (HUD-visible next boot state too).
/// Missing handle = no-op.
pub fn set_updates_note(app: &AppHandle, note: &str) {
    if let Some(h) = app.try_state::<UpdateNote>() {
        if let Ok(mut guard) = h.0.lock() {
            *guard = note.to_string();
        }
    }
    crate::tray::set_updates_text(app, note);
}

/// Check outcome. `Unreachable` covers the placeholder feed, offline
/// boxes, and malformed JSON — all silent except the tray note.
#[derive(Debug, Clone, PartialEq)]
pub enum CheckResult {
    Available(String),
    Current,
    Unreachable,
}

/// Map a check outcome to (tray note, whether to notify). Pure.
pub fn note_for(result: &CheckResult) -> (String, bool) {
    match result {
        CheckResult::Available(note) => (note.clone(), true),
        CheckResult::Current => ("Updates: up to date".to_string(), false),
        CheckResult::Unreachable => ("Updates: check unavailable".to_string(), false),
    }
}

/// Check the feed via the updater plugin. NEVER downloads or installs:
/// `check()` only fetches version metadata.
pub async fn check_for_update(app: &AppHandle) -> CheckResult {
    use tauri_plugin_updater::UpdaterExt;
    let current = env!("CARGO_PKG_VERSION");
    let updater = match app.updater() {
        Ok(u) => u,
        Err(_) => return CheckResult::Unreachable,
    };
    match updater.check().await {
        Ok(Some(update)) => {
            let latest = update.version.to_string();
            match update_note(current, &latest) {
                Some(note) => CheckResult::Available(note),
                None => CheckResult::Current,
            }
        }
        Ok(None) => CheckResult::Current,
        Err(_) => CheckResult::Unreachable,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn newer_versions_detected() {
        assert!(is_newer("0.1.0", "0.2.0"));
        assert!(is_newer("0.1.0", "0.1.1"));
        assert!(is_newer("0.1.9", "0.2.0"));
        assert!(is_newer("0.1.0", "1.0.0"));
        assert!(!is_newer("0.2.0", "0.2.0"));
        assert!(!is_newer("0.2.0", "0.1.9"));
        assert!(!is_newer("1.0.0", "0.9.9"));
    }

    #[test]
    fn staged_newer_version_produces_tray_note() {
        assert_eq!(
            update_note("0.1.0", "0.2.0"),
            Some("Updates: 0.2.0 available (current 0.1.0)".to_string())
        );
        assert_eq!(update_note("0.2.0", "0.2.0"), None);
        assert_eq!(update_note("0.3.0", "0.2.0"), None);
    }

    #[test]
    fn check_outcomes_map_to_notes() {
        let (note, notify) = note_for(&CheckResult::Available(
            "Updates: 0.2.0 available (current 0.1.0)".into(),
        ));
        assert!(note.contains("0.2.0") && notify);
        let (note, notify) = note_for(&CheckResult::Current);
        assert_eq!(note, "Updates: up to date");
        assert!(!notify);
        // Placeholder feed / offline: quiet tray note, never a scary popup.
        let (note, notify) = note_for(&CheckResult::Unreachable);
        assert_eq!(note, "Updates: check unavailable");
        assert!(!notify);
    }

    #[test]
    fn updater_config_wired_with_placeholder_feed() {
        // Guards the wiring without booting the GUI: plugin section
        // present, unreachable-by-design endpoint, unset pubkey, and NO
        // auto-install dialog flag that could prompt installs.
        let conf = std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tauri.conf.json"),
        )
        .expect("tauri.conf.json readable");
        let v: serde_json::Value = serde_json::from_str(&conf).expect("tauri.conf.json parses");
        let updater = &v["plugins"]["updater"];
        assert!(updater.is_object(), "plugins.updater section present");
        let endpoints = updater["endpoints"]
            .as_array()
            .expect("updater endpoints array");
        assert!(
            endpoints
                .iter()
                .any(|e| e.as_str() == Some(FEED_PLACEHOLDER)),
            "placeholder feed wired"
        );
        assert!(
            updater["pubkey"]
                .as_str()
                .unwrap_or("")
                .starts_with("UNSET-"),
            "pubkey is an unset placeholder (signing deferred)"
        );
        assert!(
            updater.get("dialog").is_none() || updater["dialog"].as_bool() == Some(false),
            "no install dialog in v1"
        );
    }

    #[test]
    fn no_auto_install_in_v1() {
        // Regression guard for "checks only": the download/install entry
        // points must not exist anywhere in the shell. (Needles are built
        // with concat! so this very test never trips its own scan; prose
        // elsewhere phrases it as "download/install path".)
        let needles = [
            concat!("download_and_", "install("),
            concat!("install_and_", "relaunch("),
        ];
        let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut hits = Vec::new();
        for entry in std::fs::read_dir(&src).expect("src readable") {
            let path = entry.expect("dir entry").path();
            if path.extension().and_then(|e| e.to_str()) != Some("rs") {
                continue;
            }
            let text = std::fs::read_to_string(&path).expect("source readable");
            for (i, line) in text.lines().enumerate() {
                let code = line.split("//").next().unwrap_or("");
                if needles.iter().any(|n| code.contains(n)) {
                    hits.push(format!("{}:{}", path.display(), i + 1));
                }
            }
        }
        assert!(hits.is_empty(), "auto-install calls leaked in: {hits:?}");
    }
}
