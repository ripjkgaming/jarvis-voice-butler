//! Native tray only screen controls. No Tauri command or single-instance CLI verb.
use std::io::{Read, Write};
use std::net::Shutdown;
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

#[derive(Clone, Copy)]
pub enum Action {
    Stop,
    ResumeQuiet,
    Hangup,
}

fn wire(action: Action) -> &'static [u8] {
    match action {
        Action::Stop => b"stop\n",
        Action::ResumeQuiet => b"resume_quiet\n",
        Action::Hangup => b"hangup\n",
    }
}

pub fn configured() -> bool {
    std::env::var_os("JARVIS_SCREEN_CONTROL_SOCKET")
        .map(PathBuf::from)
        .is_some_and(|path| path.is_absolute())
}

pub fn native_only() -> bool {
    std::env::var("JARVIS_SCREEN_NATIVE_ONLY").as_deref() == Ok("1")
}

fn reply(raw: &[u8]) -> Result<&'static str, String> {
    let text = std::str::from_utf8(raw).map_err(|_| "Invalid screen control result")?;
    let fields: Vec<_> = text.trim_end_matches('\n').split(' ').collect();
    if fields.len() != 3 || fields[0] != "ok" || fields[2].parse::<u64>().is_err() {
        return Err("Screen control unconfirmed; use Stop and check the call".into());
    }
    match fields[1] {
        "stopped" => Ok("Screen voice stopped; call preserved"),
        "listening" => Ok("Screen voice resumed; capture still requires an explicit request"),
        "closed" => Ok("Screen call ended"),
        _ => Err("Unrecognized screen control result".into()),
    }
}

pub fn send(action: Action) -> Result<&'static str, String> {
    let path = std::env::var_os("JARVIS_SCREEN_CONTROL_SOCKET")
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .ok_or("Screen control socket is not explicitly configured")?;
    let mut stream = UnixStream::connect(path).map_err(|_| "Screen host is unavailable")?;
    stream
        .set_read_timeout(Some(Duration::from_secs(40)))
        .map_err(|_| "Screen control timeout setup failed")?;
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .map_err(|_| "Screen control timeout setup failed")?;
    stream
        .write_all(wire(action))
        .map_err(|_| "Screen control unconfirmed")?;
    stream
        .shutdown(Shutdown::Write)
        .map_err(|_| "Screen control unconfirmed")?;
    let mut raw = Vec::new();
    stream
        .take(129)
        .read_to_end(&mut raw)
        .map_err(|_| "Screen control unconfirmed")?;
    if raw.len() > 128 {
        return Err("Screen control reply exceeded its bound".into());
    }
    reply(&raw)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_acceptance_route_is_explicit_and_default_off() {
        let old = std::env::var_os("JARVIS_SCREEN_NATIVE_ONLY");
        std::env::remove_var("JARVIS_SCREEN_NATIVE_ONLY");
        assert!(!native_only());
        std::env::set_var("JARVIS_SCREEN_NATIVE_ONLY", "true");
        assert!(!native_only());
        std::env::set_var("JARVIS_SCREEN_NATIVE_ONLY", "1");
        assert!(native_only());
        match old {
            Some(value) => std::env::set_var("JARVIS_SCREEN_NATIVE_ONLY", value),
            None => std::env::remove_var("JARVIS_SCREEN_NATIVE_ONLY"),
        }
    }

    #[test]
    fn native_wire_has_no_client_auth_or_capture_grant_fields() {
        assert_eq!(wire(Action::Stop), b"stop\n");
        assert_eq!(wire(Action::ResumeQuiet), b"resume_quiet\n");
        assert_eq!(wire(Action::Hangup), b"hangup\n");
    }

    #[test]
    fn replies_require_confirmed_bounded_lifecycle_results() {
        assert!(reply(b"ok stopped 0\n").is_ok());
        assert!(reply(b"ok listening 2\n").is_ok());
        assert!(reply(b"ok closed 0\n").is_ok());
        for raw in [
            b"error refused\n".as_slice(),
            b"ok sent 1\n",
            b"ok listening NaN\n",
            b"ok stopped 0\nok listening 2\n",
        ] {
            assert!(reply(raw).is_err());
        }
    }
}
