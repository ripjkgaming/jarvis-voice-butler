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

use std::path::PathBuf;
use std::time::Duration;

use crate::env_cfg::{self, AGENT_NAME_DEFAULT};

#[tauri::command]
pub fn app_config() -> serde_json::Value {
    let agent_name = std::env::var("AGENT_NAME")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| AGENT_NAME_DEFAULT.to_string());
    serde_json::json!({ "agentName": agent_name })
}

#[tauri::command]
pub async fn mint_token(
    room_config: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let _ = room_config; // reserved: agent dispatch stays AGENT_NAME (v1)
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
        cmd.arg(&script)
            .envs(env_cfg::sidecar_env(&repo))
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_config_always_names_an_agent() {
        let v = app_config();
        let name = v.get("agentName").and_then(|n| n.as_str()).unwrap();
        assert!(!name.is_empty());
    }
}
