//! Fail-soft health checks for the tray status dot.
//!
//! Deliberately dependency-free (std TCP + manual HTTP/1.0 + serde_json):
//! a missing bridge, dead port, or truncated body degrades one field, never
//! the shell. All combinators are pure and unit-tested.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use serde::Deserialize;

const CONNECT_TIMEOUT: Duration = Duration::from_millis(800);
const READ_TIMEOUT: Duration = Duration::from_secs(2);

/// Restart backoff ladder (seconds). After the last delay the sidecar is
/// marked Down and the tray goes red instead of looping forever.
pub const BACKOFF_SECS: [u64; 5] = [1, 2, 4, 8, 16];

/// Subset of bridge `/status` the tray actually needs.
#[derive(Debug, Deserialize, PartialEq)]
pub struct BridgeStatus {
    #[serde(default)]
    pub ok: bool,
    #[serde(default)]
    pub pipeline: String,
    #[serde(default)]
    pub local_unlocked: bool,
    #[serde(default)]
    pub livekit_configured: bool,
}

/// Overall shell state from per-sidecar aliveness + bridge reachability.
#[derive(Debug, Clone, PartialEq)]
pub enum ShellState {
    /// Manager hasn't published yet (tray shows "starting…").
    Starting,
    /// Everything the shell started is alive and the bridge answers.
    Running,
    /// Sidecars alive but bridge silent (tray still functional).
    Degraded(String),
    /// A sidecar died past its restart budget, or nothing started.
    Down(String),
}

/// Pure combinator: fold per-sidecar aliveness + bridge state into one.
pub fn combine_status(alive: &[(&str, bool)], bridge: Option<&BridgeStatus>) -> ShellState {
    let dead: Vec<&str> = alive
        .iter()
        .filter_map(|(name, ok)| if *ok { None } else { Some(*name) })
        .collect();
    if !dead.is_empty() {
        return ShellState::Down(format!("down: {}", dead.join(", ")));
    }
    match bridge {
        Some(s) if s.ok => ShellState::Running,
        Some(_) => ShellState::Degraded("bridge unhealthy".into()),
        None => ShellState::Degraded("bridge unreachable".into()),
    }
}

/// TCP connect probe (livekit-server :7880, bridge :4317).
pub fn tcp_reachable(port: u16) -> bool {
    let addr: SocketAddr = format!("127.0.0.1:{port}")
        .parse()
        .expect("loopback parses");
    TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT).is_ok()
}

/// Minimal HTTP GET over a plain TCP stream. No TLS (loopback only), no
/// redirects, no chunked encoding — the bridge speaks plain HTTP/1.0.
pub fn http_get_json(port: u16, path: &str) -> Result<serde_json::Value, String> {
    let addr: SocketAddr = format!("127.0.0.1:{port}")
        .parse()
        .map_err(|e| format!("bad addr: {e}"))?;
    let mut stream =
        TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT).map_err(|e| format!("{e}"))?;
    stream
        .set_read_timeout(Some(READ_TIMEOUT))
        .map_err(|e| format!("{e}"))?;
    stream
        .set_write_timeout(Some(CONNECT_TIMEOUT))
        .map_err(|e| format!("{e}"))?;
    write!(stream, "GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").map_err(|e| format!("{e}"))?;
    let mut buf = Vec::with_capacity(4096);
    stream.read_to_end(&mut buf).map_err(|e| format!("{e}"))?;
    let text = String::from_utf8_lossy(&buf);
    let body = text.split("\r\n\r\n").nth(1).unwrap_or("");
    serde_json::from_str(body).map_err(|e| format!("bad json: {e}"))
}

/// Typed bridge `/status` fetch. `None` = unreachable/unparseable (degraded,
/// not fatal).
pub fn bridge_status(port: u16) -> Option<BridgeStatus> {
    http_get_json(port, "/status")
        .ok()
        .and_then(|v| serde_json::from_value(v).ok())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use std::net::TcpListener;
    use std::thread;

    #[test]
    fn combine_all_alive_bridge_ok_is_running() {
        let alive = [("livekit", true), ("agent", true)];
        let b = BridgeStatus {
            ok: true,
            pipeline: "realtime".into(),
            local_unlocked: true,
            livekit_configured: true,
        };
        assert_eq!(combine_status(&alive, Some(&b)), ShellState::Running);
    }

    #[test]
    fn combine_dead_sidecar_is_down_even_with_bridge() {
        let alive = [("livekit", true), ("agent", false)];
        assert!(matches!(combine_status(&alive, None), ShellState::Down(_)));
    }

    #[test]
    fn combine_no_bridge_is_degraded_not_down() {
        let alive = [("livekit", true)];
        assert!(matches!(
            combine_status(&alive, None),
            ShellState::Degraded(_)
        ));
    }

    #[test]
    fn backoff_ladder_is_monotonic_and_capped() {
        assert_eq!(BACKOFF_SECS.len(), 5);
        for w in BACKOFF_SECS.windows(2) {
            assert!(w[0] <= w[1]);
        }
        assert!(*BACKOFF_SECS.last().unwrap() <= 16);
    }

    fn fake_bridge(body: &'static str) -> (u16, thread::JoinHandle<()>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let handle = thread::spawn(move || {
            if let Ok((mut s, _)) = listener.accept() {
                // Drain the full request head: closing with unread bytes
                // in the receive buffer produces RST instead of FIN.
                let mut req = Vec::new();
                let mut tmp = [0u8; 256];
                while !req.windows(4).any(|w| w == b"\r\n\r\n") {
                    match s.read(&mut tmp) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => req.extend_from_slice(&tmp[..n]),
                    }
                    if req.len() > 8192 {
                        break;
                    }
                }
                let resp = format!(
                    "HTTP/1.0 200 OK\r\nContent-Length: {}\r\n\r\n{}",
                    body.len(),
                    body
                );
                let _ = s.write_all(resp.as_bytes());
                let _ = s.shutdown(std::net::Shutdown::Write);
            }
        });
        (port, handle)
    }

    #[test]
    fn http_get_parses_bridge_body() {
        let (port, h) = fake_bridge(r#"{"ok": true, "pipeline": "realtime"}"#);
        let v = http_get_json(port, "/status").expect("parses");
        assert_eq!(v["pipeline"], "realtime");
        h.join().ok();
    }

    #[test]
    fn tcp_probe_fails_closed_port() {
        assert!(!tcp_reachable(1));
    }
}
