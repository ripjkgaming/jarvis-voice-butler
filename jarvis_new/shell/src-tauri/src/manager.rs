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
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use tokio::process::{Child, Command};
use tokio::sync::watch;

use crate::env_cfg::{self, SidecarPrograms, BRIDGE_PORT_DEFAULT};
use crate::health::{self, ShellState, BACKOFF_SECS};

/// Per-sidecar log rotation: when the current file exceeds
/// [`LOG_ROTATE_BYTES`], it becomes `.1` and older backups shift up to
/// `.N`; the oldest is dropped. Std-only rolling writer, no new deps.
pub const LOG_ROTATE_BYTES: u64 = 10 * 1024 * 1024;
pub const LOG_ROTATE_KEEP: u32 = 3;

/// Enforce rotation on `path` (parameterized core; production passes the
/// consts above). Missing or small file = no-op success. Rename failures
/// other than a raced-away source propagate to the caller.
pub fn rotate_log(path: &Path, max_bytes: u64, keep: u32) -> std::io::Result<()> {
    let size = match std::fs::metadata(path) {
        Ok(m) => m.len(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(e) => return Err(e),
    };
    if size <= max_bytes {
        return Ok(());
    }
    let numbered = |i: u32| path.with_extension(format!("log.{i}"));
    if keep == 0 {
        std::fs::remove_file(path)?;
        return Ok(());
    }
    let _ = std::fs::remove_file(numbered(keep)); // oldest drops, absent is fine
    for i in (1..keep).rev() {
        let (from, to) = (numbered(i), numbered(i + 1));
        if from.is_file() {
            match std::fs::rename(&from, &to) {
                Ok(()) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(e),
            }
        }
    }
    std::fs::rename(path, numbered(1))?;
    Ok(())
}

/// Title + body for a sidecar-death notification. Pure (unit-tested); the
/// watchdog passes the result to [`crate::tray::desktop_notify`].
/// `detail` already names the sidecar (e.g. "agent restart budget spent").
pub fn down_notice(detail: &str) -> (String, String) {
    (
        "Jarvis sidecar down".to_string(),
        format!("{detail} — run `jarvis diagnostics`"),
    )
}

/// True only on the transition INTO Down (so the watchdog notifies once
/// per death, not on every 2s poll while it stays down).
pub fn entered_down(old: &SidecarState, new: &SidecarState) -> bool {
    !matches!(old, SidecarState::Down(_)) && matches!(new, SidecarState::Down(_))
}

/// Set a sidecar Down, notifying once on entry. The tray goes red via the
/// next `announce`; the notification is the part that used to be silent.
fn mark_down(state: &mut SidecarState, reason: String) {
    let old = state.clone();
    *state = SidecarState::Down(reason.clone());
    if entered_down(&old, state) {
        let (title, body) = down_notice(&reason);
        crate::tray::desktop_notify(&title, &body);
    }
}

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

/// Exact-argv fingerprint match. Pure (unit-tested).
pub fn cmdline_matches(want: &[String], got: &[String]) -> bool {
    want.len() == got.len() && want.iter().zip(got.iter()).all(|(a, b)| a == b)
}

/// Kill leftover sidecars from a previous shell that died without running
/// its kill-tree (SIGKILL, `cargo tauri dev` watcher rebuilds, crashes).
/// Matches OUR exact argv fingerprints for our uid only; the single-instance
/// plugin guarantees one shell, so anything matching is stale by definition.
/// Runs BEFORE the first spawn (we have no children of our own yet) and
/// never touches our own pid. Returns pids signaled.
pub fn reap_stale_sidecars(specs: &[SidecarSpec]) -> Vec<u32> {
    let fingerprints: Vec<Vec<String>> = specs
        .iter()
        .map(|s| {
            let mut v = vec![s.program.to_string_lossy().into_owned()];
            v.extend(s.args.clone());
            v
        })
        .collect();
    let self_pid = std::process::id();
    let mut signaled = Vec::new();
    let Ok(proc) = std::fs::read_dir("/proc") else {
        return signaled;
    };
    for entry in proc.flatten() {
        let pid: u32 = match entry.file_name().to_string_lossy().parse() {
            Ok(p) => p,
            Err(_) => continue,
        };
        if pid == self_pid {
            continue;
        }
        let cmdline = match std::fs::read(entry.path().join("cmdline")) {
            Ok(b) => b,
            Err(_) => continue,
        };
        let parts: Vec<String> = cmdline
            .split(|b| *b == 0)
            .filter(|s| !s.is_empty())
            .map(|s| String::from_utf8_lossy(s).into_owned())
            .collect();
        if fingerprints.iter().any(|f| cmdline_matches(f, &parts)) {
            // Util-linux /bin/kill is always present on the target distros;
            // stale sidecars are not our children, so init reaps them.
            if std::process::Command::new("kill")
                .arg(pid.to_string())
                .status()
                .map(|s| s.success())
                .unwrap_or(false)
            {
                signaled.push(pid);
            }
        }
    }
    if !signaled.is_empty() {
        eprintln!("jarvis: reaped {} stale sidecar(s)", signaled.len());
    }
    signaled
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
            let reason = format!("{} failed to start", procs[i].0.name);
            mark_down(&mut procs[i].2, reason);
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
                    let reason = format!("{} restart budget spent", entry.0.name);
                    mark_down(&mut entry.2, reason);
                }
                changed = true;
            } else {
                let reason = format!("{} down", spec.name);
                mark_down(state, reason);
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
    // Best-effort: a failed rotation must never fail a spawn.
    let _ = rotate_log(&log_path, LOG_ROTATE_BYTES, LOG_ROTATE_KEEP);
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
    fn cmdline_fingerprint_matches_exactly() {
        let want = vec!["/venv/bin/python".to_string(), "src/a.py".to_string()];
        assert!(cmdline_matches(&want, &want.clone()));
        assert!(!cmdline_matches(&want, &["/venv/bin/python".to_string()]));
        assert!(!cmdline_matches(
            &want,
            &["/venv/bin/python".to_string(), "src/b.py".to_string()]
        ));
    }

    #[test]
    fn reap_with_no_match_signals_nothing() {
        let specs = vec![SidecarSpec {
            name: "never",
            program: PathBuf::from("/nonexistent-probe-xyz/python"),
            args: vec!["__no_such_script_xyz__.py".into()],
            ready: ReadyCheck::ProcessAlive,
            ready_timeout_s: 1,
        }];
        assert!(reap_stale_sidecars(&specs).is_empty());
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

    fn tmp_log(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("jarvis-rotate-test-{name}"));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p.join("sidecar.log")
    }

    #[test]
    fn rotate_is_noop_when_missing_or_small() {
        let path = tmp_log("noop");
        rotate_log(&path, 1024, 2).expect("missing file is fine");
        std::fs::write(&path, b"small").unwrap();
        rotate_log(&path, 1024, 2).expect("small file untouched");
        assert_eq!(std::fs::read(&path).unwrap(), b"small");
        assert!(!path.with_extension("log.1").exists());
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn rotate_shifts_backups_and_drops_oldest() {
        let path = tmp_log("shift");
        std::fs::write(&path, b"current-over-limit-payload").unwrap();
        std::fs::write(path.with_extension("log.1"), b"one").unwrap();
        std::fs::write(path.with_extension("log.2"), b"two").unwrap();
        rotate_log(&path, 8, 2).expect("rotates");
        // keep=2: old .2 dropped, old .1 -> .2, overgrown current -> .1.
        assert_eq!(
            std::fs::read(path.with_extension("log.1")).unwrap(),
            b"current-over-limit-payload"
        );
        assert_eq!(std::fs::read(path.with_extension("log.2")).unwrap(), b"one");
        assert!(!path.exists()); // fresh current starts on next spawn
        let _ = std::fs::remove_dir_all(path.parent().unwrap());
    }

    #[test]
    fn down_notice_points_at_diagnostics() {
        let (title, body) = down_notice("agent restart budget spent");
        assert_eq!(title, "Jarvis sidecar down");
        assert!(body.contains("agent restart budget spent"));
        assert!(body.contains("jarvis diagnostics"));
    }

    #[test]
    fn entered_down_fires_once_per_death() {
        let ready = SidecarState::Ready;
        let down = SidecarState::Down("x".into());
        assert!(entered_down(&ready, &down));
        assert!(entered_down(&SidecarState::Starting, &down));
        assert!(!entered_down(&down, &SidecarState::Down("y".into())));
        assert!(!entered_down(&ready, &ready));
    }

    /// 30-day volume simulation through the REAL rotation consts.
    ///
    /// Traffic model (measured 2026-09-18 on the live box, steady state
    /// excluding startup bursts, with margin): the agent is the chattiest
    /// (~4KB/h of capacity chatter) plus one ~110KB warmup burst per daily
    /// restart; livekit/bridge/wake stay under ~3KB/h. actions.log traffic
    /// (~100 short tool lines/day) never nears its 5MB cap, so it ships
    /// unrotated. Total must stay under the 50MB accept.
    #[test]
    fn thirty_day_simulated_volume_stays_under_50mb() {
        use std::io::Write;
        let dir = std::env::temp_dir().join("jarvis-rotate-test-30d");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // (name, bytes per simulated hour, daily bursts of 110KB)
        let model = [
            ("agent", 10 * 1024, true),
            ("livekit", 3 * 1024, false),
            ("bridge", 3 * 1024, false),
            ("wake", 3 * 1024, false),
        ];
        let line = vec![b'x'; 256];
        for hour in 0..(30 * 24) {
            for (name, hourly, bursts) in &model {
                let path = dir.join(format!("{name}.log"));
                {
                    let mut f = std::fs::OpenOptions::new()
                        .create(true)
                        .append(true)
                        .open(&path)
                        .unwrap();
                    for _ in 0..(*hourly / 256) {
                        f.write_all(&line).unwrap();
                    }
                    if *bursts && hour % 24 == 0 {
                        for _ in 0..(110 * 1024 / 256) {
                            f.write_all(&line).unwrap();
                        }
                    }
                }
                rotate_log(&path, LOG_ROTATE_BYTES, LOG_ROTATE_KEEP).unwrap();
            }
        }
        // actions.log: 100 tool lines/day, capped like src/system/log_action.
        let actions = dir.join("actions.log");
        for _ in 0..(30 * 100) {
            if actions.is_file() && actions.metadata().unwrap().len() > 5 * 1024 * 1024 {
                std::fs::rename(&actions, dir.join("actions.log.1")).unwrap();
            }
            let mut f = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&actions)
                .unwrap();
            f.write_all(&[b'y'; 200]).unwrap();
        }
        let total: u64 = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().metadata().unwrap().len())
            .sum();
        // Rotation demonstrably engaged on the agent log…
        assert!(dir.join("agent.log.1").is_file());
        // …and a month of traffic still fits the accept with margin.
        assert!(
            total < 50 * 1024 * 1024,
            "30-day simulated volume {total} bytes exceeds 50MB"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
