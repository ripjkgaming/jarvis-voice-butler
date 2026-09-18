//! Supervised sidecar manager: spawn order, readiness gates, restarts.
//!
//! Start order (each gate must pass before the next spawns):
//!   livekit-server (TCP :7880) → agent worker (alive + livekit reachable,
//!   90s budget for model warmup) → wake_client (alive) → bridge (HTTP
//!   /health :4317). Overall state is published on a watch channel for the
//!   tray. Unexpected exits restart on the [`BACKOFF_SECS`] ladder (max 5),
//!   then the sidecar is marked Down and the tray goes red. Quit kills the
//!   whole tree — no orphan python/livekit processes.

use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use tokio::process::{Child, Command};
use tokio::sync::watch;

use crate::env_cfg::{self, SidecarPrograms, BRIDGE_PORT_DEFAULT};
use crate::health::{self, ShellState, BACKOFF_SECS};

/// How the manager knows a sidecar is ready.
#[derive(Debug, Clone)]
pub enum ReadyCheck {
    /// TCP port accepts connections (livekit-server).
    TcpPort(u16),
    /// Plain-HTTP 200 with `"ok": true` (bridge /health).
    HttpOk { port: u16, path: String },
    /// Process alive AND livekit reachable (agent worker: registration
    /// implies a live server; the framework logs no stable marker).
    AgentAlive { livekit_port: u16 },
    /// Process alive (wake_client: mic loop has no port).
    ProcessAlive,
}

#[derive(Debug, Clone)]
pub struct SidecarSpec {
    pub name: &'static str,
    pub program: PathBuf,
    pub args: Vec<String>,
    pub ready: ReadyCheck,
    /// Seconds before the readiness gate gives up (agent warms up models).
    pub ready_timeout_s: u64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum SidecarState {
    Starting,
    Ready,
    Restarting(u8),
    Down(String),
}

/// Build the four specs from resolved programs. Pure (no spawning).
pub fn build_specs(
    progs: &SidecarPrograms,
    livekit_port: u16,
    bridge_port: u16,
) -> Vec<SidecarSpec> {
    let repo = &progs.repo;
    vec![
        SidecarSpec {
            name: "livekit",
            program: progs.livekit_bin.clone(),
            args: vec![
                "--config".into(),
                env_cfg::jarvis_home()
                    .join("livekit.yaml")
                    .to_string_lossy()
                    .into_owned(),
            ],
            ready: ReadyCheck::TcpPort(livekit_port),
            ready_timeout_s: 30,
        },
        SidecarSpec {
            name: "agent",
            program: progs.agent_python.clone(),
            args: vec![
                repo.join("src")
                    .join("agent.py")
                    .to_string_lossy()
                    .into_owned(),
                "start".into(),
            ],
            ready: ReadyCheck::AgentAlive { livekit_port },
            ready_timeout_s: 90,
        },
        SidecarSpec {
            name: "wake",
            program: progs.wake_python.clone(),
            args: vec![repo
                .join("src")
                .join("wake_client.py")
                .to_string_lossy()
                .into_owned()],
            ready: ReadyCheck::ProcessAlive,
            ready_timeout_s: 15,
        },
        SidecarSpec {
            name: "bridge",
            program: progs.bridge_python.clone(),
            args: vec![repo
                .join("src")
                .join("bridge.py")
                .to_string_lossy()
                .into_owned()],
            ready: ReadyCheck::HttpOk {
                port: bridge_port,
                path: "/health".into(),
            },
            ready_timeout_s: 15,
        },
    ]
}

fn check_ready(check: &ReadyCheck) -> bool {
    match check {
        ReadyCheck::TcpPort(p) => health::tcp_reachable(*p),
        ReadyCheck::HttpOk { port, path } => health::http_get_json(*port, path)
            .ok()
            .and_then(|v| v.get("ok").and_then(|b| b.as_bool()))
            .unwrap_or(false),
        ReadyCheck::AgentAlive { livekit_port } => health::tcp_reachable(*livekit_port),
        ReadyCheck::ProcessAlive => true,
    }
}

async fn wait_ready(spec: &SidecarSpec, child: &mut Child) -> bool {
    let deadline = tokio::time::Instant::now() + Duration::from_secs(spec.ready_timeout_s);
    loop {
        if child.try_wait().ok().flatten().is_some() {
            return false; // exited before becoming ready
        }
        if check_ready(&spec.ready) {
            return true;
        }
        if tokio::time::Instant::now() >= deadline {
            return false;
        }
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

/// Supervise all sidecars until `shutdown` fires. Publishes [`ShellState`].
pub async fn supervise(
    specs: Vec<SidecarSpec>,
    env: HashMap<String, String>,
    log_dir: PathBuf,
    state_tx: watch::Sender<ShellState>,
    mut shutdown: watch::Receiver<bool>,
) {
    let mut procs: Vec<(SidecarSpec, Option<Child>, SidecarState, u8)> = specs
        .into_iter()
        .map(|s| (s, None, SidecarState::Starting, 0))
        .collect();

    let announce = |procs: &[(SidecarSpec, Option<Child>, SidecarState, u8)],
                    tx: &watch::Sender<ShellState>| {
        let alive: Vec<(&str, bool)> = procs
            .iter()
            .map(|(s, _, st, _)| {
                (
                    s.name,
                    matches!(st, SidecarState::Ready | SidecarState::Restarting(_)),
                )
            })
            .collect();
        let bridge_port = progs_bridge_port(&env);
        let bridge = health::bridge_status(bridge_port);
        let _ = tx.send(health::combine_status(&alive, bridge.as_ref()));
    };

    for i in 0..procs.len() {
        if *shutdown.borrow() {
            break;
        }
        // Sequential gates: later sidecars need earlier ones up.
        let ok = start_and_gate(&mut procs[i], &env, &log_dir).await;
        if !ok {
            procs[i].3 = BACKOFF_SECS.len() as u8; // gate failed: mark down
            procs[i].2 = SidecarState::Down(format!("{} failed to start", procs[i].0.name));
        }
        announce(&procs, &state_tx);
    }

    // Watchdog: restart unexpected exits on the backoff ladder.
    loop {
        tokio::select! {
            _ = shutdown.changed() => {
                if *shutdown.borrow() {
                    break;
                }
            }
            _ = tokio::time::sleep(Duration::from_secs(2)) => {}
        }
        let mut changed = false;
        for entry in procs.iter_mut() {
            let (spec, child, state, restarts) = entry;
            let exited = match child {
                Some(c) => c.try_wait().ok().flatten().is_some(),
                None => !matches!(state, SidecarState::Down(_)),
            };
            if !exited {
                continue;
            }
            *child = None;
            if (*restarts as usize) < BACKOFF_SECS.len() {
                let delay = BACKOFF_SECS[*restarts as usize];
                *restarts += 1;
                *state = SidecarState::Restarting(*restarts);
                // Wait out this rung of the ladder, then respawn inline so
                // start order stays sequential and observable.
                tokio::time::sleep(Duration::from_secs(delay)).await;
                let ok = start_and_gate(entry, &env, &log_dir).await;
                if ok {
                    entry.2 = SidecarState::Ready;
                } else if (entry.3 as usize) >= BACKOFF_SECS.len() {
                    entry.2 = SidecarState::Down(format!("{} restart budget spent", entry.0.name));
                }
                changed = true;
            } else {
                *state = SidecarState::Down(format!("{} down", spec.name));
                changed = true;
            }
        }
        if changed {
            announce(&procs, &state_tx);
        }
    }

    // Shutdown: kill the whole tree, then wait (no orphans).
    for (spec, child, _, _) in procs.iter_mut() {
        if let Some(c) = child {
            let _ = c.start_kill();
            let _ = tokio::time::timeout(Duration::from_secs(5), c.wait()).await;
        }
        let _ = spec;
    }
}

async fn start_and_gate(
    entry: &mut (SidecarSpec, Option<Child>, SidecarState, u8),
    env: &HashMap<String, String>,
    log_dir: &std::path::Path,
) -> bool {
    let (spec, child, state, _) = entry;
    *state = SidecarState::Starting;
    let log_path = log_dir.join(format!("{}.log", spec.name));
    let log_file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .ok();
    let (stdout_cfg, stderr_cfg) = match log_file {
        Some(f) => {
            let f2 = f.try_clone().ok();
            match f2 {
                Some(g) => (Stdio::from(f), Stdio::from(g)),
                None => (Stdio::null(), Stdio::null()),
            }
        }
        None => (Stdio::null(), Stdio::null()),
    };
    let mut cmd = Command::new(&spec.program);
    cmd.args(&spec.args)
        .envs(env)
        .stdout(stdout_cfg)
        .stderr(stderr_cfg);
    match cmd.spawn() {
        Ok(mut c) => {
            let ready = wait_ready(spec, &mut c).await;
            *child = Some(c);
            if ready {
                *state = SidecarState::Ready;
            }
            ready
        }
        Err(e) => {
            *state = SidecarState::Down(format!("spawn {}: {e}", spec.name));
            false
        }
    }
}

fn progs_bridge_port(env: &HashMap<String, String>) -> u16 {
    env.get("JARVIS_BRIDGE_PORT")
        .and_then(|s| s.parse().ok())
        .unwrap_or(BRIDGE_PORT_DEFAULT)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::env_cfg::SidecarPrograms;

    fn fake_progs() -> SidecarPrograms {
        SidecarPrograms {
            livekit_bin: PathBuf::from("/bin/true"),
            agent_python: PathBuf::from("/bin/true"),
            wake_python: PathBuf::from("/bin/true"),
            bridge_python: PathBuf::from("/bin/true"),
            repo: PathBuf::from("/repo"),
            missing: vec![],
        }
    }

    #[test]
    fn specs_start_with_livekit_and_end_with_bridge() {
        let specs = build_specs(&fake_progs(), 7880, 4317);
        assert_eq!(specs.len(), 4);
        assert_eq!(specs.first().unwrap().name, "livekit");
        assert_eq!(specs.last().unwrap().name, "bridge");
        assert!(matches!(specs[0].ready, ReadyCheck::TcpPort(7880)));
    }

    #[test]
    fn agent_gets_the_longest_readiness_budget() {
        let specs = build_specs(&fake_progs(), 7880, 4317);
        let agent = specs.iter().find(|s| s.name == "agent").unwrap();
        assert!(specs
            .iter()
            .all(|s| agent.ready_timeout_s >= s.ready_timeout_s));
    }
}
