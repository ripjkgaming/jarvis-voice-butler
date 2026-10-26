//! Exact target-only KWin helper. Legitimate own executable entitlement required.
//! No desktop/active-window capture, output path, activation, input or persistence.
use dbus::arg::{OwnedFd, PropMap, Variant};
use dbus::blocking::Connection;
use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::fd::FromRawFd;
use std::time::{Duration, Instant};

const MAX_BYTES: usize = 64 * 1024 * 1024;
const MAX_PIXELS: usize = 16 * 1024 * 1024;
const TIMEOUT: Duration = Duration::from_secs(6);
type Result<T> = std::result::Result<T, &'static str>;

#[derive(Clone, Debug, PartialEq)]
struct Target {
    uuid: String,
    app: String,
    pid: u32,
    width: u32,
    height: u32,
}
fn uuid(value: &str) -> Option<String> {
    let text = value
        .strip_prefix('{')
        .and_then(|v| v.strip_suffix('}'))
        .unwrap_or(value);
    if text.len() != 36 {
        return None;
    }
    for (i, c) in text.chars().enumerate() {
        if [8, 13, 18, 23].contains(&i) {
            if c != '-' {
                return None;
            }
        } else if !c.is_ascii_hexdigit() {
            return None;
        }
    }
    if text.chars().all(|c| c == '0' || c == '-') {
        return None;
    }
    Some(text.to_ascii_lowercase())
}
fn text<'a>(map: &'a PropMap, name: &str) -> Result<&'a str> {
    map.get(name)
        .and_then(|v| v.0.as_str())
        .ok_or("invalid_metadata")
}
fn number(map: &PropMap, name: &str) -> Result<u32> {
    let value = map.get(name).ok_or("invalid_metadata")?;
    let n = value
        .0
        .as_u64()
        .or_else(|| value.0.as_i64().and_then(|n| u64::try_from(n).ok()))
        .ok_or("invalid_metadata")?;
    u32::try_from(n).map_err(|_| "invalid_metadata")
}
fn geometry_number(map: &PropMap, name: &str) -> Result<u32> {
    if let Some(value) = map.get(name).and_then(|v| v.0.as_f64()) {
        if !value.is_finite() || value <= 0.0 || value.fract() != 0.0 || value > f64::from(u32::MAX)
        {
            return Err("invalid_geometry");
        }
        return Ok(value as u32);
    }
    number(map, name)
}
fn boolean(map: &PropMap, name: &str) -> Result<bool> {
    let value = map.get(name).ok_or("invalid_metadata")?;
    value
        .0
        .as_any()
        .downcast_ref::<bool>()
        .copied()
        .ok_or("invalid_metadata")
}
fn parse_target(map: &PropMap, expected: &Target) -> Result<Target> {
    if uuid(text(map, "uuid")?) != Some(expected.uuid.clone())
        || number(map, "pid")? != expected.pid
        || text(map, "resourceClass")? != expected.app
        || !boolean(map, "localhost")?
        || boolean(map, "minimized")?
        || boolean(map, "excludeFromCapture")?
    {
        return Err("target_unavailable_or_changed");
    }
    let width = geometry_number(map, "width")?;
    let height = geometry_number(map, "height")?;
    bounds(
        width,
        height,
        width.checked_mul(4).ok_or("invalid_geometry")?,
    )?;
    Ok(Target {
        uuid: expected.uuid.clone(),
        app: expected.app.clone(),
        pid: expected.pid,
        width,
        height,
    })
}
fn bounds(width: u32, height: u32, stride: u32) -> Result<usize> {
    let pixels = (width as usize)
        .checked_mul(height as usize)
        .ok_or("invalid_geometry")?;
    let size = (stride as usize)
        .checked_mul(height as usize)
        .ok_or("invalid_geometry")?;
    if width == 0
        || height == 0
        || width > 16384
        || height > 16384
        || pixels > MAX_PIXELS
        || stride < width.saturating_mul(4)
        || stride > width.saturating_mul(4) + 4096
        || size > MAX_BYTES
    {
        return Err("invalid_geometry");
    }
    Ok(size)
}
fn remaining(deadline: Instant) -> Result<Duration> {
    let left = deadline
        .checked_duration_since(Instant::now())
        .ok_or("operation_timeout")?;
    if left.is_zero() {
        return Err("operation_timeout");
    }
    Ok(left.min(TIMEOUT))
}
fn compositor_owner(connection: &Connection, capture: bool, deadline: Instant) -> Result<String> {
    let bus = connection.with_proxy(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        remaining(deadline)?,
    );
    let (owner,): (String,) = bus
        .method_call("org.freedesktop.DBus", "GetNameOwner", ("org.kde.KWin",))
        .map_err(|_| "compositor_unavailable")?;
    let (uid,): (u32,) = bus
        .method_call("org.freedesktop.DBus", "GetConnectionUnixUser", (&owner,))
        .map_err(|_| "compositor_untrusted")?;
    let (pid,): (u32,) = bus
        .method_call(
            "org.freedesktop.DBus",
            "GetConnectionUnixProcessID",
            (&owner,),
        )
        .map_err(|_| "compositor_untrusted")?;
    if uid != unsafe { libc::getuid() }
        || std::fs::read_to_string(format!("/proc/{pid}/comm"))
            .map_err(|_| "compositor_untrusted")?
            .trim()
            != "kwin_wayland"
    {
        return Err("compositor_untrusted");
    }
    if capture {
        let (screenshot_owner,): (String,) = bus
            .method_call(
                "org.freedesktop.DBus",
                "GetNameOwner",
                ("org.kde.KWin.ScreenShot2",),
            )
            .map_err(|_| "capture_service_unavailable")?;
        if screenshot_owner != owner {
            return Err("capture_service_untrusted");
        }
    }
    Ok(owner)
}
fn query_map(
    connection: &Connection,
    owner: &str,
    target: &Target,
    deadline: Instant,
) -> Result<PropMap> {
    let proxy = connection.with_proxy(owner, "/KWin", remaining(deadline)?);
    let (map,): (PropMap,) = proxy
        .method_call(
            "org.kde.KWin",
            "getWindowInfo",
            (format!("{{{}}}", target.uuid),),
        )
        .map_err(|_| "window_query_failed")?;
    Ok(map)
}
fn query(
    connection: &Connection,
    owner: &str,
    target: &Target,
    deadline: Instant,
) -> Result<Target> {
    parse_target(&query_map(connection, owner, target, deadline)?, target)
}
fn image_metadata(map: &PropMap, target: &Target) -> Result<(u32, u32, u32, u32, usize)> {
    if text(map, "type")? != "raw" || uuid(text(map, "windowId")?) != Some(target.uuid.clone()) {
        return Err("capture_target_mismatch");
    }
    let format = number(map, "format")?;
    let width = number(map, "width")?;
    let height = number(map, "height")?;
    let stride = number(map, "stride")?;
    if ![4, 5, 6, 16, 17, 18].contains(&format) {
        return Err("unsupported_pixel_format");
    }
    let scale = map
        .get("scale")
        .and_then(|v| v.0.as_f64())
        .ok_or("invalid_scale")?;
    if !scale.is_finite() || !(0.25..=4.0).contains(&scale) {
        return Err("invalid_scale");
    }
    // Native resolution disabled, no decorations/shadows; target content size
    // is at most frame size. Reject impossible scale/oversize geometry.
    if width as f64 > f64::from(target.width) * scale + 2.0
        || height as f64 > f64::from(target.height) * scale + 2.0
    {
        return Err("capture_geometry_mismatch");
    }
    Ok((
        format,
        width,
        height,
        stride,
        bounds(width, height, stride)?,
    ))
}
fn rgba(raw: &[u8], format: u32, width: u32, height: u32, stride: u32) -> Result<Vec<u8>> {
    let expected = bounds(width, height, stride)?;
    if raw.len() != expected {
        return Err("truncated_pixels");
    }
    let mut out = Vec::with_capacity(width as usize * height as usize * 4);
    for row in raw.chunks_exact(stride as usize) {
        for px in row[..width as usize * 4].as_chunks::<4>().0 {
            let (mut r, mut g, mut b, a) = if [4, 5, 6].contains(&format) {
                (px[2], px[1], px[0], if format == 4 { 255 } else { px[3] })
            } else {
                (px[0], px[1], px[2], if format == 16 { 255 } else { px[3] })
            };
            if [6, 18].contains(&format) {
                if a == 0 {
                    r = 0;
                    g = 0;
                    b = 0;
                } else {
                    let unpremultiply = |v: u8| {
                        ((u32::from(v) * 255 + u32::from(a) / 2) / u32::from(a)).min(255) as u8
                    };
                    r = unpremultiply(r);
                    g = unpremultiply(g);
                    b = unpremultiply(b);
                }
            }
            out.extend_from_slice(&[r, g, b, a]);
        }
    }
    Ok(out)
}
fn capture(
    connection: &Connection,
    owner: &str,
    target: &Target,
    deadline: Instant,
) -> Result<Vec<u8>> {
    let before = query(connection, owner, target, deadline)?;
    // A memfd is anonymous RAM: KWin never receives a persistent output path.
    let rawfd = unsafe {
        libc::memfd_create(
            c"jarvis-watch-pixels".as_ptr(),
            libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING,
        )
    };
    if rawfd < 0 {
        return Err("memory_buffer_unavailable");
    }
    let mut file = unsafe { File::from_raw_fd(rawfd) };
    file.set_len(MAX_BYTES as u64)
        .map_err(|_| "memory_buffer_unavailable")?;
    // Limit the anonymous buffer in the kernel even before metadata arrives.
    if unsafe {
        libc::fcntl(
            rawfd,
            libc::F_ADD_SEALS,
            libc::F_SEAL_GROW | libc::F_SEAL_SHRINK,
        )
    } < 0
    {
        return Err("memory_buffer_unavailable");
    }
    let send: OwnedFd = file
        .try_clone()
        .map_err(|_| "memory_buffer_unavailable")?
        .into();
    let options: HashMap<&str, Variant<bool>> = HashMap::from([
        ("include-decoration", Variant(false)),
        ("include-shadow", Variant(false)),
        ("include-cursor", Variant(false)),
        ("native-resolution", Variant(false)),
    ]);
    let proxy = connection.with_proxy(owner, "/org/kde/KWin/ScreenShot2", TIMEOUT);
    let (metadata,): (PropMap,) = proxy
        .method_call(
            "org.kde.KWin.ScreenShot2",
            "CaptureWindow",
            (format!("{{{}}}", target.uuid), options, send),
        )
        .map_err(|error| {
            if error.name() == Some("org.kde.KWin.ScreenShot2.Error.NoAuthorized") {
                "helper_permission_required"
            } else {
                "window_capture_failed"
            }
        })?;
    let (format, width, height, stride, size) = image_metadata(&metadata, &before)?;
    let pixel_deadline = (Instant::now() + TIMEOUT).min(deadline);
    loop {
        let len = file.stream_position().map_err(|_| "pixel_read_failed")?;
        if len > size as u64 {
            return Err("oversized_pixels");
        }
        if len == size as u64 {
            break;
        }
        if Instant::now() >= pixel_deadline {
            return Err("pixel_read_timeout");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let after = query(connection, owner, target, deadline)?;
    if compositor_owner(connection, true, deadline)? != owner {
        return Err("compositor_changed");
    }
    if before != after {
        return Err("target_changed_during_capture");
    }
    file.seek(SeekFrom::Start(0))
        .map_err(|_| "pixel_read_failed")?;
    let mut raw = vec![0; size];
    file.read_exact(&mut raw).map_err(|_| "pixel_read_failed")?;
    drop(file);
    let pixels = rgba(&raw, format, width, height, stride)?;
    raw.fill(0);
    let mut png_bytes = Vec::new();
    let mut encoder = png::Encoder::new(&mut png_bytes, width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    encoder
        .write_header()
        .map_err(|_| "png_encoding_failed")?
        .write_image_data(&pixels)
        .map_err(|_| "png_encoding_failed")?;
    if png_bytes.len() > 32 * 1024 * 1024 {
        return Err("png_size_exceeded");
    }
    Ok(png_bytes)
}
fn run() -> Result<()> {
    if !cfg!(target_endian = "little") {
        return Err("unsupported_byte_order");
    }
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 5 || !["--probe", "--inspect", "--capture"].contains(&args[1].as_str()) {
        return Err("invalid_arguments");
    }
    let uuid = uuid(&args[2]).ok_or("invalid_window_id")?;
    let pid = args[3].parse::<u32>().map_err(|_| "invalid_pid")?;
    if pid == 0
        || args[4].is_empty()
        || args[4].len() > 200
        || args[4].chars().any(char::is_control)
    {
        return Err("invalid_target");
    }
    let target = Target {
        uuid,
        app: args[4].clone(),
        pid,
        width: 0,
        height: 0,
    };
    let duration = if args[1] == "--capture" {
        Duration::from_secs(12)
    } else {
        Duration::from_secs(1)
    };
    let deadline = Instant::now() + duration;
    // This guard also bounds libdbus authentication and CPU work, which do not
    // accept a method deadline. It exits only this owned one-shot helper.
    std::thread::spawn(move || {
        std::thread::sleep(duration);
        eprintln!("operation_timeout");
        std::process::exit(1);
    });
    let connection = Connection::new_session().map_err(|_| "session_bus_unavailable")?;
    let owner = compositor_owner(&connection, args[1] == "--capture", deadline)?;
    if args[1] != "--capture" {
        let metadata = query_map(&connection, &owner, &target, deadline)?;
        let selected = parse_target(&metadata, &target)?;
        let caption: String = text(&metadata, "caption")?
            .chars()
            .filter(|c| !c.is_control())
            .take(300)
            .collect();
        let json = serde_json::json!({"uuid":selected.uuid,"app":selected.app,"pid":selected.pid,"width":selected.width,"height":selected.height,"caption":caption});
        println!("{json}");
    } else {
        std::io::stdout()
            .write_all(&capture(&connection, &owner, &target, deadline)?)
            .map_err(|_| "output_closed")?;
    }
    Ok(())
}
fn main() {
    if let Err(code) = run() {
        eprintln!("{code}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn anonymous_buffer_is_sealed_and_tracks_shared_partial_writes() {
        let fd = unsafe {
            libc::memfd_create(
                c"jarvis-synthetic-buffer-test".as_ptr(),
                libc::MFD_CLOEXEC | libc::MFD_ALLOW_SEALING,
            )
        };
        assert!(fd >= 0);
        let mut reader = unsafe { File::from_raw_fd(fd) };
        reader.set_len(8).unwrap();
        assert_eq!(
            unsafe {
                libc::fcntl(
                    fd,
                    libc::F_ADD_SEALS,
                    libc::F_SEAL_GROW | libc::F_SEAL_SHRINK,
                )
            },
            0
        );
        let mut writer = reader.try_clone().unwrap();
        writer.write_all(&[1, 2, 3, 4]).unwrap();
        assert_eq!(reader.stream_position().unwrap(), 4);
        assert_eq!(reader.metadata().unwrap().len(), 8);
        writer.write_all(&[5, 6, 7, 8]).unwrap();
        assert_eq!(reader.stream_position().unwrap(), 8);
        assert!(writer.write_all(&[9]).is_err());
        assert!(reader.set_len(9).is_err());
        assert!(reader.set_len(7).is_err());
        reader.seek(SeekFrom::Start(0)).unwrap();
        let mut output = vec![];
        reader.read_to_end(&mut output).unwrap();
        assert_eq!(output, vec![1, 2, 3, 4, 5, 6, 7, 8]);
    }
    #[test]
    fn total_operation_deadline_never_renews() {
        assert_eq!(
            remaining(Instant::now() - Duration::from_millis(1)),
            Err("operation_timeout")
        );
        assert!(remaining(Instant::now() + Duration::from_secs(12)).unwrap() <= TIMEOUT);
    }
    #[test]
    fn capture_metadata_is_bound_to_requested_window_and_native_size() {
        let target = Target {
            uuid: "abcdef12-1234-5678-90ab-1234567890ab".into(),
            app: "synthetic".into(),
            pid: 123,
            width: 640,
            height: 300,
        };
        let mut map = PropMap::new();
        map.insert("type".into(), Variant(Box::new("raw".to_string())));
        map.insert("windowId".into(), Variant(Box::new(target.uuid.clone())));
        map.insert("format".into(), Variant(Box::new(6u32)));
        map.insert("width".into(), Variant(Box::new(640u32)));
        map.insert("height".into(), Variant(Box::new(300u32)));
        map.insert("stride".into(), Variant(Box::new(2560u32)));
        map.insert("scale".into(), Variant(Box::new(1.0f64)));
        assert_eq!(
            image_metadata(&map, &target),
            Ok((6, 640, 300, 2560, 768000))
        );
        map.insert(
            "windowId".into(),
            Variant(Box::new("11111111-1234-5678-90ab-1234567890ab".to_string())),
        );
        assert_eq!(
            image_metadata(&map, &target),
            Err("capture_target_mismatch")
        );
        map.insert("windowId".into(), Variant(Box::new(target.uuid.clone())));
        map.insert("scale".into(), Variant(Box::new(f64::NAN)));
        assert_eq!(image_metadata(&map, &target), Err("invalid_scale"));
    }
    #[test]
    fn geometry_double_is_finite_integral_and_pid_is_not_coerced() {
        let mut map = PropMap::new();
        map.insert("width".into(), Variant(Box::new(640.0f64)));
        assert_eq!(geometry_number(&map, "width"), Ok(640));
        assert_eq!(number(&map, "width"), Err("invalid_metadata"));
        for value in [640.5, f64::NAN, f64::INFINITY, -1.0] {
            map.insert("width".into(), Variant(Box::new(value)));
            assert_eq!(geometry_number(&map, "width"), Err("invalid_geometry"));
        }
    }
    #[test]
    fn uuid_normalization_and_rejection() {
        assert_eq!(
            uuid("{ABCDEF12-1234-5678-90AB-1234567890AB}"),
            Some("abcdef12-1234-5678-90ab-1234567890ab".into())
        );
        for value in [
            "",
            "../../private",
            "00000000-0000-0000-0000-000000000000",
            "12345678901234567890123456789012345678",
        ] {
            assert_eq!(uuid(value), None);
        }
    }
    #[test]
    fn geometry_rejects_overflow_padding_and_oversize() {
        assert_eq!(bounds(640, 300, 2560), Ok(768000));
        for args in [
            (0, 100, 400),
            (100, 100, 399),
            (100, 100, 9999),
            (u32::MAX, 2, u32::MAX),
            (16000, 16000, 64000),
        ] {
            assert!(bounds(args.0, args.1, args.2).is_err());
        }
    }
    #[test]
    fn pixel_order_and_premultiplication() {
        assert_eq!(
            rgba(&[10, 20, 30, 255], 5, 1, 1, 4).unwrap(),
            vec![30, 20, 10, 255]
        );
        assert_eq!(
            rgba(&[25, 50, 100, 128], 6, 1, 1, 4).unwrap(),
            vec![199, 100, 50, 128]
        );
        assert_eq!(rgba(&[1, 2, 3, 0], 6, 1, 1, 4).unwrap(), vec![0, 0, 0, 0]);
        assert_eq!(
            rgba(&[1, 2, 3, 0], 16, 1, 1, 4).unwrap(),
            vec![1, 2, 3, 255]
        );
        assert!(rgba(&[1, 2], 5, 1, 1, 4).is_err());
    }
    #[test]
    fn zero_padding_is_not_part_of_pixels() {
        assert_eq!(
            rgba(&[1, 2, 3, 255, 99, 99, 99, 99], 17, 1, 1, 8).unwrap(),
            vec![1, 2, 3, 255]
        );
    }
}
