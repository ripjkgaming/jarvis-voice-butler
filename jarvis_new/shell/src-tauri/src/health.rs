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
    http_get_json_authed(port, path, None)
}

/// GET with an optional Bearer token (the bridge gates every route when
/// `JARVIS_BRIDGE_TOKEN` is set, including reads like `/mic`).
pub fn http_get_json_authed(
    port: u16,
    path: &str,
    token: Option<&str>,
) -> Result<serde_json::Value, String> {
    let clean = token.unwrap_or("").replace(['\r', '\n'], "");
    let mut head = format!("GET {path} HTTP/1.0\r\nHost: 127.0.0.1\r\n");
    if !clean.is_empty() {
        head += &format!("Authorization: Bearer {clean}\r\n");
    }
    head += "\r\n";
    let body = http_roundtrip(port, &head, &[])?;
    serde_json::from_str(&body).map_err(|e| format!("bad json: {e}"))
}

/// Minimal HTTP POST with a JSON body (bridge `/mic`). Same loopback-only
/// constraints as [`http_get_json`]; `token` adds the Bearer header the
/// bridge gates on when `JARVIS_BRIDGE_TOKEN` is set.
pub fn http_post_json(
    port: u16,
    path: &str,
    body: &serde_json::Value,
    token: Option<&str>,
) -> Result<serde_json::Value, String> {
    let text = serde_json::to_string(body).map_err(|e| format!("bad body: {e}"))?;
    // Our own env feeds the header: strip CR/LF so a weird value can only
    // fail auth, never smuggle a second header.
    let clean_token = token.unwrap_or("").replace(['\r', '\n'], "");
    let mut head = format!(
        "POST {path} HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {}\r\n",
        text.len()
    );
    if !clean_token.is_empty() {
        head += &format!("Authorization: Bearer {clean_token}\r\n");
    }
    head += "\r\n";
    let resp = http_roundtrip(port, &head, text.as_bytes())?;
    serde_json::from_str(&resp).map_err(|e| format!("bad json: {e}"))
}

/// One blocking request/response over loopback TCP. Returns the body text.
fn http_roundtrip(port: u16, head: &str, body: &[u8]) -> Result<String, String> {
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
    stream
        .write_all(head.as_bytes())
        .map_err(|e| format!("{e}"))?;
    stream.write_all(body).map_err(|e| format!("{e}"))?;
    let mut buf = Vec::with_capacity(4096);
    stream.read_to_end(&mut buf).map_err(|e| format!("{e}"))?;
    let text = String::from_utf8_lossy(&buf);
    Ok(text.split("\r\n\r\n").nth(1).unwrap_or("").to_string())
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

    /// Fake bridge that captures the full request head + body, then replies
    /// a canned JSON body. Returns (port, captured_request, join handle).
    fn fake_bridge_capture(
        reply: &'static str,
    ) -> (
        u16,
        std::sync::mpsc::Receiver<String>,
        thread::JoinHandle<()>,
    ) {
        use std::sync::mpsc;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel();
        let handle = thread::spawn(move || {
            if let Ok((mut s, _)) = listener.accept() {
                let mut req = Vec::new();
                let mut tmp = [0u8; 256];
                // Read head first (ends at blank line), then the body per
                // Content-Length so POST assertions see the full payload.
                while !req.windows(4).any(|w| w == b"\r\n\r\n") {
                    match s.read(&mut tmp) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => req.extend_from_slice(&tmp[..n]),
                    }
                    if req.len() > 8192 {
                        break;
                    }
                }
                let head_end = req
                    .windows(4)
                    .position(|w| w == b"\r\n\r\n")
                    .map(|i| i + 4)
                    .unwrap_or(req.len());
                let head = String::from_utf8_lossy(&req[..head_end]).into_owned();
                let want: usize = head
                    .lines()
                    .find_map(|l| {
                        l.strip_prefix("Content-Length:")
                            .and_then(|v| v.trim().parse().ok())
                    })
                    .unwrap_or(0);
                while req.len() < head_end + want {
                    match s.read(&mut tmp) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => req.extend_from_slice(&tmp[..n]),
                    }
                }
                let _ = tx.send(String::from_utf8_lossy(&req).into_owned());
                let resp = format!(
                    "HTTP/1.0 200 OK\r\nContent-Length: {}\r\n\r\n{}",
                    reply.len(),
                    reply
                );
                let _ = s.write_all(resp.as_bytes());
                let _ = s.shutdown(std::net::Shutdown::Write);
            }
        });
        (port, rx, handle)
    }

    #[test]
    fn http_post_sends_json_body_with_bearer() {
        let (port, rx, h) = fake_bridge_capture(r#"{"ok": true, "muted": true}"#);
        let v = http_post_json(
            port,
            "/mic",
            &serde_json::json!({"muted": true}),
            Some("s3cret"),
        )
        .expect("parses");
        assert_eq!(v["muted"], true);
        let req = rx.recv_timeout(Duration::from_secs(5)).expect("request");
        assert!(req.starts_with("POST /mic "), "{req}");
        assert!(req.contains("Authorization: Bearer s3cret"), "{req}");
        assert!(req.ends_with(r#"{"muted":true}"#), "{req}");
        h.join().ok();
    }

    #[test]
    fn http_post_without_token_sends_no_auth_header() {
        let (port, rx, h) = fake_bridge_capture(r#"{"ok": true}"#);
        http_post_json(port, "/mic", &serde_json::json!({"muted": false}), None).expect("parses");
        let req = rx.recv_timeout(Duration::from_secs(5)).expect("request");
        assert!(!req.contains("Authorization:"), "{req}");
        h.join().ok();
    }

    #[test]
    fn tcp_probe_fails_closed_port() {
        assert!(!tcp_reachable(1));
    }
}
