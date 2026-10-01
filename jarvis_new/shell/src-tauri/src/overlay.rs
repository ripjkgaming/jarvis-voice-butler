//! Overlay window behaviors: geometry persist/restore + blur-hide helpers.
//!
//! The overlay is a small always-on-top HUD summoned with Super+J. Its last
//! position/size survives restarts via `~/.jarvis/overlay.json` (written on
//! move/resize, read back at boot). All file helpers are pure-ish and
//! fail-soft: a missing or corrupt file yields `None` (caller falls back to
//! the `tauri.conf.json` defaults), never a boot failure.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

/// Persisted overlay geometry. Plain data; the caller applies it with
/// `set_position` / `set_size` so this module stays unit-testable without
/// a display server.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct OverlayGeometry {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

/// File name under `$JARVIS_HOME` holding the geometry.
pub const OVERLAY_GEOMETRY_FILE: &str = "overlay.json";

/// Canonical 16:9 HUD size. The whole HUD (both regions) is laid out for
/// this; anything smaller crops it.
pub const OVERLAY_WIDTH: u32 = 1280;
pub const OVERLAY_HEIGHT: u32 = 720;

/// True when a persisted geometry predates the 16:9 HUD (or was hand-edited
/// into something unusable): too small, or nowhere near 16:9. Pure.
pub fn needs_migration(geom: &OverlayGeometry) -> bool {
    if geom.width < 1000 || geom.height < 600 {
        return true;
    }
    let ratio = geom.width as f64 / geom.height as f64;
    (ratio - 16.0 / 9.0).abs() > 0.25
}

/// `$JARVIS_HOME/overlay.json` for an explicit home dir (pure).
pub fn geometry_path(home: &Path) -> PathBuf {
    home.join(OVERLAY_GEOMETRY_FILE)
}

/// Load persisted geometry. `None` when the file is missing, unreadable,
/// or corrupt — the caller must fall back to compiled-in defaults.
pub fn load_geometry(path: &Path) -> Option<OverlayGeometry> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

/// Persist geometry, creating parent dirs. Errors propagate to the caller
/// (which logs and moves on — a failed save must never hide the window).
pub fn save_geometry(path: &Path, geometry: &OverlayGeometry) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let text = serde_json::to_string(geometry)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    std::fs::write(path, text)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_file(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("jarvis-overlay-test-{name}"))
    }

    #[test]
    fn geometry_path_lives_under_jarvis_home() {
        assert_eq!(
            geometry_path(Path::new("/tmp/fake-home")),
            PathBuf::from("/tmp/fake-home/overlay.json")
        );
    }

    #[test]
    fn round_trip_preserves_geometry() {
        let path = tmp_file("roundtrip.json");
        let _ = std::fs::remove_file(&path);
        let geom = OverlayGeometry {
            x: 120,
            y: 80,
            width: OVERLAY_WIDTH,
            height: OVERLAY_HEIGHT,
        };
        save_geometry(&path, &geom).expect("save");
        assert_eq!(load_geometry(&path), Some(geom));
        std::fs::remove_file(&path).ok();
    }

    #[test]
    fn missing_file_loads_as_none() {
        let path = tmp_file("does-not-exist.json");
        let _ = std::fs::remove_file(&path);
        assert_eq!(load_geometry(&path), None);
    }

    #[test]
    fn corrupt_file_loads_as_none() {
        let path = tmp_file("corrupt.json");
        std::fs::write(&path, "{ not json").expect("write");
        assert_eq!(load_geometry(&path), None);
        std::fs::remove_file(&path).ok();
    }

    #[test]
    fn save_creates_missing_parent_dirs() {
        let dir = std::env::temp_dir().join("jarvis-overlay-test-nested/sub");
        let _ = std::fs::remove_dir_all(std::env::temp_dir().join("jarvis-overlay-test-nested"));
        let path = dir.join("overlay.json");
        let geom = OverlayGeometry {
            x: 0,
            y: 0,
            width: OVERLAY_WIDTH,
            height: OVERLAY_HEIGHT,
        };
        save_geometry(&path, &geom).expect("save with fresh parents");
        assert_eq!(load_geometry(&path), Some(geom));
        std::fs::remove_dir_all(std::env::temp_dir().join("jarvis-overlay-test-nested")).ok();
    }

    #[test]
    fn old_portrait_geometry_needs_migration() {
        let old = OverlayGeometry {
            x: 0,
            y: 0,
            width: 420,
            height: 640,
        };
        assert!(needs_migration(&old));
        let good = OverlayGeometry {
            x: 0,
            y: 0,
            width: OVERLAY_WIDTH,
            height: OVERLAY_HEIGHT,
        };
        assert!(!needs_migration(&good));
    }
}
