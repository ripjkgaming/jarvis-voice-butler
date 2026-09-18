//! Environment + path resolution for Jarvis sidecars.
//!
//! The launcher (this shell) is the single place that sets the variables the
//! audit proved sidecars need: `JARVIS_LOCAL=1` (every system tool refuses
//! without it), `PYTHONPATH=src` (the `local_voice` fallback imports),
//! `LIVEKIT_URL` (local server, Phase 0), `JARVIS_HOME` (sandboxed homes).
//! All constructors here are pure and unit-tested.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub const LIVEKIT_PORT: u16 = 7880;
pub const BRIDGE_PORT_DEFAULT: u16 = 4317;
pub const AGENT_NAME_DEFAULT: &str = "my-agent";

/// Locate the `jarvis_new` repo checkout.
///
/// 1. `JARVIS_REPO` env (installer/first-run wizard writes this).
/// 2. Walk up from the current executable looking for `src/agent.py`.
/// 3. Current working directory (dev: `cargo tauri dev` from `shell/`).
pub fn repo_root() -> PathBuf {
    if let Some(p) = std::env::var_os("JARVIS_REPO") {
        let p = PathBuf::from(p);
        if is_repo_root(&p) {
            return p;
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut dir: &Path = &exe;
        for _ in 0..6 {
            if let Some(parent) = dir.parent() {
                dir = parent;
                if is_repo_root(dir) {
                    return dir.to_path_buf();
                }
            } else {
                break;
            }
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        let mut dir: &Path = &cwd;
        for _ in 0..4 {
            if is_repo_root(dir) {
                return dir.to_path_buf();
            }
            if let Some(parent) = dir.parent() {
                dir = parent;
            } else {
                break;
            }
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn is_repo_root(p: &Path) -> bool {
    p.join("src").join("agent.py").is_file() && p.join("pyproject.toml").is_file()
}

/// Home dir honoring the `JARVIS_HOME` override (desktop sandbox support).
pub fn jarvis_home() -> PathBuf {
    if let Some(h) = std::env::var_os("JARVIS_HOME") {
        return PathBuf::from(h);
    }
    dirs_home().join(".jarvis")
}

fn dirs_home() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/root"))
}

/// Base environment every sidecar gets. Inherits the parent env and
/// overrides/adds only the Jarvis contract. Never touches secrets.
pub fn sidecar_env(repo: &Path) -> HashMap<String, String> {
    let mut env: HashMap<String, String> = std::env::vars().collect();
    env.insert("JARVIS_LOCAL".into(), "1".into());
    env.insert(
        "PYTHONPATH".into(),
        prepend_path(
            &repo.join("src").to_string_lossy(),
            env.get("PYTHONPATH").map(String::as_str).unwrap_or(""),
        ),
    );
    env.entry("LIVEKIT_URL".into())
        .or_insert_with(|| format!("ws://127.0.0.1:{LIVEKIT_PORT}"));
    env.entry("JARVIS_HOME".into())
        .or_insert(jarvis_home().to_string_lossy().into_owned());
    env.entry("AGENT_NAME".into())
        .or_insert_with(|| AGENT_NAME_DEFAULT.into());
    env
}

fn prepend_path(first: &str, rest: &str) -> String {
    if rest.is_empty() {
        first.to_string()
    } else {
        format!("{first}:{rest}")
    }
}

/// Resolved programs for the four sidecars. Missing binaries are reported,
/// never panicked on — the manager surfaces them in the tray.
#[derive(Debug, Clone)]
pub struct SidecarPrograms {
    pub livekit_bin: PathBuf,
    pub agent_python: PathBuf,
    pub wake_python: PathBuf,
    pub bridge_python: PathBuf,
    pub repo: PathBuf,
    pub missing: Vec<String>,
}

pub fn sidecar_programs(repo: &Path) -> SidecarPrograms {
    let livekit_bin = std::env::var_os("JARVIS_LIVEKIT_BIN")
        .map(PathBuf::from)
        .unwrap_or_else(|| jarvis_home().join("bin").join("livekit-server"));
    let agent_python = repo.join(".venv").join("bin").join("python");
    let wake_python = repo.join(".venv-wake").join("bin").join("python");
    let mut missing = Vec::new();
    for (label, p) in [
        ("livekit-server", &livekit_bin),
        ("agent venv python", &agent_python),
        ("wake venv python", &wake_python),
    ] {
        if !p.is_file() {
            // PATH fallback is intentional: `uv run` wrappers and dev
            // machines may not have .venv inside the repo.
            let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("");
            if which_on_path(name).is_none() {
                missing.push(format!("{label} not found at {}", p.display()));
            }
        }
    }
    SidecarPrograms {
        livekit_bin,
        agent_python,
        wake_python,
        bridge_python: agent_python_clone(repo),
        repo: repo.to_path_buf(),
        missing,
    }
}

fn agent_python_clone(repo: &Path) -> PathBuf {
    repo.join(".venv").join("bin").join("python")
}

fn which_on_path(name: &str) -> Option<PathBuf> {
    if name.is_empty() {
        return None;
    }
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths)
            .map(|dir| dir.join(name))
            .find(|p| p.is_file())
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sidecar_env_sets_the_contract() {
        let env = sidecar_env(Path::new("/repo"));
        assert_eq!(env.get("JARVIS_LOCAL").map(String::as_str), Some("1"));
        assert!(env["PYTHONPATH"].starts_with("/repo/src"));
        assert_eq!(
            env.get("LIVEKIT_URL").map(String::as_str),
            Some("ws://127.0.0.1:7880")
        );
    }

    #[test]
    fn sidecar_env_never_overrides_user_values() {
        std::env::set_var("AGENT_NAME", "custom-test-agent");
        let env = sidecar_env(Path::new("/repo"));
        assert_eq!(
            env.get("AGENT_NAME").map(String::as_str),
            Some("custom-test-agent")
        );
        std::env::remove_var("AGENT_NAME");
        let env2 = sidecar_env(Path::new("/repo"));
        let _ = env2.get("AGENT_NAME").expect("agent name always set");
    }

    #[test]
    fn repo_root_honors_jarvis_repo() {
        let tmp = std::env::temp_dir().join("jarvis-env-test");
        std::fs::create_dir_all(tmp.join("src")).ok();
        std::fs::write(tmp.join("src").join("agent.py"), "# fake").ok();
        std::fs::write(tmp.join("pyproject.toml"), "# fake").ok();
        std::env::set_var("JARVIS_REPO", &tmp);
        assert_eq!(repo_root(), tmp);
        std::env::remove_var("JARVIS_REPO");
        std::fs::remove_dir_all(&tmp).ok();
    }
}
