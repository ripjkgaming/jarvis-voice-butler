//! School mode: the overlay becomes Jarvis's own taskbar, laid exactly over
//! the bottom panel (full width, full panel height) like a skin, above it.
//! With no bottom panel it floats as a slim strip bottom-right instead.
//!
//! It must never get in the way: no decorations, no taskbar entry, and it
//! never takes keyboard focus (`set_focusable(false)`), so Sir keeps typing
//! wherever he was. It does take clicks: its app buttons switch windows
//! like the real taskbar underneath. Wayland gives clients no say over position, so a
//! one-shot KWin script docks it (and restores the saved HUD geometry on
//! exit). Mode state lives in `~/.jarvis/mode.json`, written by the bridge;
//! `jarvis-shell schoolon|schooloff` applies it live.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use tauri::{AppHandle, Manager};

/// Requested size (logical px); KWin then fits it over the whole panel.
/// The width is the no-panel fallback strip's.
pub const STRIP_WIDTH: f64 = 620.0;
pub const STRIP_HEIGHT: f64 = 48.0;
/// Gap from the working area's bottom-right corner when there is no
/// bottom panel to dock into (logical px).
pub const STRIP_MARGIN: i32 = 12;

/// Window title while in school mode: the KWin rule below matches it
/// exactly, so it never touches the normal HUD ("Jarvis").
pub const STRIP_TITLE: &str = "Jarvis \u{b7} School";
pub const HUD_TITLE: &str = "Jarvis";
const RULE_ID: &str = "jarvis-school-strip";

static SCHOOL: AtomicBool = AtomicBool::new(false);

pub fn is_school() -> bool {
    SCHOOL.load(Ordering::SeqCst)
}

/// Mode recorded on disk ("school" → true). Missing/corrupt → false.
pub fn school_on_disk(home: &std::path::Path) -> bool {
    std::fs::read_to_string(home.join("mode.json"))
        .ok()
        .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
        .and_then(|v| v.get("mode").and_then(|m| m.as_str()).map(|m| m == "school"))
        .unwrap_or(false)
}

/// JS computing `top`/`panel` for window `w`: the bottom panel's real top
/// edge. The reserved strip (full minus maximize area) can be shorter than
/// the panel as drawn (floating panels overhang it by a few px, which then
/// peek above the bar), so the tallest bottom dock on the same output wins.
const PANEL_TOP_JS: &str = "const full = workspace.clientArea(KWin.FullScreenArea, w);\n\
    const a = workspace.clientArea(KWin.MaximizeArea, w);\n\
    let top = a.y + a.height;\n\
    for (const d of workspace.windowList()) {\n\
      if (!d.dock || d.output !== w.output) continue;\n\
      const g = d.frameGeometry;\n\
      if (g.y > full.y + full.height / 2 && g.y < top) top = g.y;\n\
    }\n\
    const panel = (full.y + full.height) - top;\n";

/// Connector name (e.g. "DP-1") of Plasma's primary display: the output
/// `kscreen-doctor -j` reports at priority 1. None off Plasma / on error.
pub fn primary_output_name() -> Option<String> {
    let out = std::process::Command::new("kscreen-doctor").arg("-j").output().ok()?;
    parse_primary_output(&String::from_utf8_lossy(&out.stdout))
}

/// Pick the enabled priority-1 output out of `kscreen-doctor -j` JSON.
/// Only names safe to splice into a JS string pass. Pure.
pub fn parse_primary_output(json: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    v.get("outputs")?.as_array()?.iter().find_map(|o| {
        let primary = o.get("priority").and_then(|p| p.as_i64()) == Some(1)
            && o.get("enabled").and_then(|e| e.as_bool()).unwrap_or(true);
        let name = o.get("name")?.as_str()?;
        let safe = !name.is_empty()
            && name.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c));
        (primary && safe).then(|| name.to_string())
    })
}

/// KWin script that docks the strip (school) or puts the HUD back.
/// Matches our overlay by exact caption "Jarvis" on a jarvis-shell window.
/// The strip docks onto `primary` (connector name) when KWin knows it,
/// else onto the screen Sir is working on.
pub fn kwin_script(school: bool, restore: Option<(i32, i32, u32, u32)>, primary: Option<&str>) -> String {
    let body = if school {
        format!(
            "const target = workspace.screens.find(o => o.name === \"{primary}\") || workspace.activeScreen;\n\
             if (target && w.output !== target) workspace.sendClientToScreen(w, target);\n\
             {top_js}\
             const s = w.frameGeometry;\n\
             w.noBorder = true;\n\
             if (panel >= 24) {{\n\
               w.frameGeometry = {{x: full.x, y: top, width: full.width, height: panel}};\n\
             }} else {{\n\
               w.frameGeometry = {{x: a.x + a.width - s.width - {m}, y: a.y + a.height - s.height - {m}, width: s.width, height: s.height}};\n\
             }}\n\
             w.keepAbove = true; w.skipTaskbar = true; w.skipPager = true; w.skipSwitcher = true; w.onAllDesktops = true;",
            m = STRIP_MARGIN,
            top_js = PANEL_TOP_JS,
            primary = primary.unwrap_or("")
        )
    } else {
        let geo = match restore {
            Some((x, y, width, height)) => format!(
                "w.frameGeometry = {{x: {x}, y: {y}, width: {width}, height: {height}}};"
            ),
            None => String::new(),
        };
        format!(
            "w.noBorder = false;\n{geo}\n\
             w.skipTaskbar = false; w.skipPager = false; w.skipSwitcher = false; w.onAllDesktops = false;"
        )
    };
    format!(
        "for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"Jarvis\" && w.caption !== \"{title}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           {body}\n\
         }}\n",
        title = STRIP_TITLE
    )
}

/// `kwriteconfig6` args for the strip's window rule. Pure.
///
/// Scripts can't make a window unfocusable; a rule can. Matches only the
/// exact school title, forcing: never take focus, keep above, and stay
/// out of the taskbar / switcher / pager.
pub fn rule_entries() -> Vec<(&'static str, &'static str)> {
    vec![
        ("Description", "Jarvis school taskbar (no focus)"),
        ("title", STRIP_TITLE),
        ("titlematch", "1"),
        ("acceptfocus", "false"),
        ("acceptfocusrule", "2"),
        ("above", "true"),
        ("aboverule", "2"),
        ("skiptaskbar", "true"),
        ("skiptaskbarrule", "2"),
        ("skipswitcher", "true"),
        ("skipswitcherrule", "2"),
        ("skippager", "true"),
        ("skippagerrule", "2"),
    ]
}

/// Rules list with ours appended once. Pure.
pub fn rules_with_ours(existing: &str) -> String {
    rules_with(existing, RULE_ID)
}

/// Rules list with `id` appended once. Pure.
pub fn rules_with(existing: &str, id: &str) -> String {
    let mut ids: Vec<&str> = existing.split(',').map(str::trim).filter(|s| !s.is_empty()).collect();
    if !ids.contains(&id) {
        ids.push(id);
    }
    ids.join(",")
}

fn ensure_rule() {
    install_rule(RULE_ID, &rule_entries());
}

/// Install a KWin window rule (idempotent) and reload rules. Fail-soft.
/// Shared with the Brave orb (orb.rs).
pub(crate) fn install_rule(rule_id: &str, entries: &[(&str, &str)]) {
    let read = std::process::Command::new("kreadconfig6")
        .args(["--file", "kwinrulesrc", "--group", "General", "--key", "rules"])
        .output();
    let existing = match read {
        Ok(o) => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        Err(_) => return, // not Plasma
    };
    let write = |group: &str, key: &str, value: &str| {
        let _ = std::process::Command::new("kwriteconfig6")
            .args(["--file", "kwinrulesrc", "--group", group, "--key", key, value])
            .output();
    };
    for (key, value) in entries {
        write(rule_id, key, value);
    }
    let rules = rules_with(&existing, rule_id);
    write("General", "count", &rules.split(',').count().to_string());
    write("General", "rules", &rules);
    let _ = std::process::Command::new("dbus-send")
        .args(["--session", "--dest=org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"])
        .output();
}

fn run_kwin(script: &str) {
    run_kwin_named(script, "jarvis_school_dock");
}

/// Load + run + unload a KWin script over D-Bus. Fail-soft (X11 / no KWin).
/// Shared with the Brave orb (orb.rs).
pub(crate) fn run_kwin_named(script: &str, name: &str) {
    let path = std::env::temp_dir().join(format!("{name}.js"));
    if std::fs::write(&path, script).is_err() {
        return;
    }
    let dbus = |args: &[&str]| {
        std::process::Command::new("dbus-send")
            .args(["--session", "--print-reply", "--dest=org.kde.KWin"])
            .args(args)
            .output()
    };
    let _ = dbus(&["/Scripting", "org.kde.kwin.Scripting.unloadScript", &format!("string:{name}")]);
    let out = match dbus(&[
        "/Scripting",
        "org.kde.kwin.Scripting.loadScript",
        &format!("string:{}", path.display()),
        &format!("string:{name}"),
    ]) {
        Ok(o) => String::from_utf8_lossy(&o.stdout).to_string(),
        Err(_) => return,
    };
    let id = out
        .split_whitespace()
        .skip_while(|t| *t != "int32")
        .nth(1)
        .and_then(|t| t.parse::<i64>().ok());
    if let Some(id) = id {
        let _ = dbus(&[&format!("/Scripting/Script{id}"), "org.kde.kwin.Script.run"]);
    }
    std::thread::sleep(std::time::Duration::from_millis(300));
    let _ = dbus(&["/Scripting", "org.kde.kwin.Scripting.unloadScript", &format!("string:{name}")]);
}

/// Turn the overlay into the strip, playing the entry transition.
///
/// The page drives the transition (school-transition.tsx) and calls back
/// through [`school_stage`] for each window change: HUD visible ->
/// "collapse" (it folds into the screen sides first), hidden -> "arrive"
/// (tracers only). [`TX_WATCHDOG_MS`] docks it anyway if the page stalls.
pub fn enter(app: &AppHandle) {
    let was = SCHOOL.swap(true, Ordering::SeqCst);
    if was {
        apply(app);
        return;
    }
    let gen = TX_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    signal(app, if overlay_visible(app) { "collapse" } else { "arrive" });
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(TX_WATCHDOG_MS));
        if is_school() && TX_GEN.load(Ordering::SeqCst) == gen && DOCKED_GEN.load(Ordering::SeqCst) != gen {
            eprintln!("jarvis: school transition stalled; docking directly");
            apply(&app);
        }
    });
}

/// Straight to the docked strip, no transition (boot restore).
pub fn enter_quiet(app: &AppHandle) {
    SCHOOL.store(true, Ordering::SeqCst);
    apply(app);
}

/// Transition generation: bumped per entry, so a stale watchdog or stage
/// call from an earlier entry never acts on a newer one.
static TX_GEN: AtomicU64 = AtomicU64::new(0);
/// Generation whose transition reached the "dock" stage.
static DOCKED_GEN: AtomicU64 = AtomicU64::new(0);
/// If the page hasn't docked the strip by now (ms), the shell does.
pub const TX_WATCHDOG_MS: u64 = 10_000;

/// KWin script reporting the geometry the entry transition needs, pushed
/// to the bridge (`SchoolGeom` on org.jarvis.Focus, read back by the page
/// at /school/geom): the HUD's content rect relative to its screen, that
/// screen, the primary screen, the primary's bottom-panel height, and
/// which way the primary lies. `nonce` is echoed so the page ignores stale
/// reports. Pure.
pub fn measure_script(nonce: &str, primary: Option<&str>) -> String {
    let nonce: String = nonce.chars().filter(|c| c.is_ascii_alphanumeric()).take(32).collect();
    format!(
        "for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"Jarvis\" && w.caption !== \"{title}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           const out = w.output;\n\
           const prim = workspace.screens.find(o => o.name === \"{primary}\") || out;\n\
           const g = w.clientGeometry, og = out.geometry, pg = prim.geometry;\n\
           const full = workspace.clientArea(KWin.FullScreenArea, prim, workspace.currentDesktop);\n\
           const a = workspace.clientArea(KWin.MaximizeArea, prim, workspace.currentDesktop);\n\
           let top = a.y + a.height;\n\
           for (const d of workspace.windowList()) {{\n\
             if (!d.dock || d.output !== prim) continue;\n\
             const f = d.frameGeometry;\n\
             if (f.y > full.y + full.height / 2 && f.y < top) top = f.y;\n\
           }}\n\
           const dx = (pg.x + pg.width / 2) - (og.x + og.width / 2);\n\
           const dy = (pg.y + pg.height / 2) - (og.y + og.height / 2);\n\
           const dir = out === prim ? null : Math.abs(dx) >= Math.abs(dy) ? (dx < 0 ? \"left\" : \"right\") : (dy < 0 ? \"up\" : \"down\");\n\
           const r = q => ({{x: Math.round(q.x), y: Math.round(q.y), w: Math.round(q.width), h: Math.round(q.height)}});\n\
           const hud = r(g); hud.x -= Math.round(og.x); hud.y -= Math.round(og.y);\n\
           callDBus(\"org.jarvis.Focus\", \"/org/jarvis/Focus\", \"org.jarvis.Focus\", \"SchoolGeom\", JSON.stringify({{\n\
             nonce: \"{nonce}\", visible: !w.minimized, hud: hud, out: r(og), primary: r(pg),\n\
             same: out === prim, dir: dir, panel: Math.round((full.y + full.height) - top)\n\
           }}));\n\
           break;\n\
         }}\n",
        title = STRIP_TITLE,
        primary = primary.unwrap_or(""),
    )
}

/// Where [`cover_script`] puts the overlay.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Cover {
    /// Drop the title bar, content pixel-for-pixel where it was (the HUD
    /// folds in place, so its viewport must not change).
    Unframe,
    /// Fill the screen the overlay is on.
    Here,
    /// Fill the primary screen.
    Primary,
    /// Fill the screen holding the centre of this rect (x, y, w, h): where
    /// the HUD returns to on the way out of school mode.
    At([i32; 4]),
}

/// KWin script laying the borderless overlay out for the transition.
/// `content` (unframe only) is the page's viewport in screen coordinates:
/// GTK draws the title bar itself (client-side), so once decorations are
/// off the window is pinned to exactly where the content already was.
/// Pure.
pub fn cover_script(cover: Cover, primary: Option<&str>, content: Option<[i32; 4]>) -> String {
    let body = match cover {
        Cover::Unframe => match content {
            Some([x, y, w, h]) => format!(
                "w.noBorder = true;\n\
                 w.frameGeometry = {{x: {x}, y: {y}, width: {w}, height: {h}}};"
            ),
            None => "const c = w.clientGeometry;\n\
                 w.noBorder = true;\n\
                 w.frameGeometry = {x: c.x, y: c.y, width: c.width, height: c.height};"
                .to_string(),
        },
        Cover::Here | Cover::Primary | Cover::At(_) => {
            let target = match cover {
                Cover::Primary => format!(
                    "workspace.screens.find(o => o.name === \"{}\") || w.output",
                    primary.unwrap_or("")
                ),
                Cover::At([x, y, w, h]) => {
                    let (cx, cy) = (x + w / 2, y + h / 2);
                    format!(
                        "workspace.screens.find(o => {{ const g = o.geometry; \
                         return {cx} >= g.x && {cx} < g.x + g.width && {cy} >= g.y && {cy} < g.y + g.height; }}) || w.output"
                    )
                }
                _ => "w.output".to_string(),
            };
            format!(
                "const target = {target};\n\
                 if (w.output !== target) workspace.sendClientToScreen(w, target);\n\
                 w.noBorder = true;\n\
                 const g = target.geometry;\n\
                 w.frameGeometry = {{x: g.x, y: g.y, width: g.width, height: g.height}};"
            )
        }
    };
    format!(
        "for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"Jarvis\" && w.caption !== \"{title}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           {body}\n\
           w.keepAbove = true; w.skipTaskbar = true; w.skipPager = true; w.skipSwitcher = true; w.onAllDesktops = true;\n\
           break;\n\
         }}\n",
        title = STRIP_TITLE,
    )
}

/// How long GTK gets to drop its title bar before KWin pins the window.
const UNFRAME_SETTLE_MS: u64 = 120;

/// Overlay props for the transition and the strip: undecorated, above,
/// out of the taskbar, never focused. Click-through while `ghost`.
fn strip_window_props(app: &AppHandle, ghost: bool) {
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        let _ = win.set_title(STRIP_TITLE);
        let _ = win.set_min_size(None::<tauri::Size>);
        let _ = win.set_decorations(false);
        let _ = win.set_skip_taskbar(true);
        let _ = win.set_always_on_top(true);
        let _ = win.set_focusable(false);
        let _ = win.set_ignore_cursor_events(ghost);
    }
}

/// One step of the page-driven entry transition. Only acts while school
/// mode is on. Stages:
/// - "measure": push geometry for `nonce` to the bridge (see measure_script)
/// - "unframe": drop the title bar, content pinned at `rect` (screen x, y,
///   width, height); click-through from here on
/// - "cover": fill the overlay's current screen
/// - "primary": fill the primary screen, showing the window if hidden
/// - "dock": lay the strip over the primary's panel (takes clicks again)
///
/// And the return transition (school-return.tsx), after [`exit`]:
/// - "cover-at": fill the screen the HUD returns to (`rect`)
/// - "restore": school mode off; the HUD window back at `rect` (its frame,
///   title bar included), decorated and focusable again
#[tauri::command]
pub fn school_stage(
    app: AppHandle,
    stage: String,
    nonce: Option<String>,
    rect: Option<[i32; 4]>,
) -> Result<(), String> {
    if !is_school() {
        return Ok(());
    }
    let gen = TX_GEN.load(Ordering::SeqCst);
    match stage.as_str() {
        "measure" => {
            let nonce = nonce.unwrap_or_default();
            std::thread::spawn(move || {
                let primary = primary_output_name();
                run_kwin_named(&measure_script(&nonce, primary.as_deref()), "jarvis_school_measure");
            });
        }
        "unframe" | "cover" | "primary" | "cover-at" => {
            let cover = match stage.as_str() {
                "unframe" => Cover::Unframe,
                "cover" => Cover::Here,
                "cover-at" => Cover::At(rect.ok_or("cover-at needs a rect")?),
                _ => Cover::Primary,
            };
            ensure_rule();
            strip_window_props(&app, true);
            if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
                let _ = win.show();
            }
            let content = if cover == Cover::Unframe { rect } else { None };
            std::thread::spawn(move || {
                // Let GTK drop its title bar first, so the pin lands on the
                // undecorated window and doesn't squeeze the old one.
                if cover == Cover::Unframe {
                    std::thread::sleep(std::time::Duration::from_millis(UNFRAME_SETTLE_MS));
                }
                let primary = primary_output_name();
                run_kwin_named(&cover_script(cover, primary.as_deref(), content), "jarvis_school_cover");
            });
        }
        "dock" => {
            DOCKED_GEN.store(gen, Ordering::SeqCst);
            ensure_rule();
            strip_window_props(&app, false);
            std::thread::spawn(|| {
                let primary = primary_output_name();
                run_kwin(&kwin_script(true, None, primary.as_deref()));
            });
        }
        "restore" => {
            RETURNING.store(false, Ordering::SeqCst);
            let frame = rect.map(|[x, y, w, h]| crate::overlay::OverlayGeometry {
                x,
                y,
                width: w.max(1) as u32,
                height: h.max(1) as u32,
            });
            restore_at(&app, frame, UNFRAME_SETTLE_MS);
        }
        other => return Err(format!("unknown school stage {other:?}")),
    }
    Ok(())
}

fn overlay_visible(app: &AppHandle) -> bool {
    app.get_webview_window(crate::commands::OVERLAY_LABEL)
        .and_then(|w| w.is_visible().ok())
        .unwrap_or(false)
}

/// Tell the page a school transition is starting ("collapse" | "expand").
/// A DOM event rather than a Tauri event: the UI has no @tauri-apps/api.
fn signal(app: &AppHandle, phase: &str) {
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        let _ = win.eval(&signal_js(phase));
    }
}

/// The JS dispatched for a transition. Pure.
pub fn signal_js(phase: &str) -> String {
    let phase = match phase {
        "expand" | "arrive" | "return" => phase,
        _ => "collapse",
    };
    format!("window.dispatchEvent(new CustomEvent('jarvis-school', {{ detail: '{phase}' }}))")
}

/// Open (or close) a taskbar menu from outside the page: the CLI verb
/// `jarvis-shell schoolmenu launcher|quick|calendar|close`, so Jarvis can
/// open them by voice too. Clicks the same buttons Sir would.
pub fn open_menu(app: &AppHandle, which: &str) {
    if !is_school() {
        return;
    }
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        let _ = win.eval(&menu_click_js(which));
    }
}

/// JS clicking the bar button for `which` (unknown -> close). Pure.
pub fn menu_click_js(which: &str) -> String {
    let sel = match which {
        "launcher" | "apps" | "start" => ".sbar-start",
        "quick" | "settings" => ".sbar-tray",
        "calendar" | "clock" => ".sbar-clock",
        _ => ".sbar-root[data-menu] button[data-open='true']",
    };
    format!("(() => {{ const b = document.querySelector(\"{sel}\"); if (b) b.click(); }})()")
}

/// Most the taskbar may grow upward for a menu (logical px).
pub const MENU_MAX_EXTRA: u32 = 900;

/// KWin script growing the docked taskbar upward by `extra` px (0 = back
/// to just the panel), keeping the bar itself exactly over the panel. Pure.
pub fn menu_script(extra: u32) -> String {
    let extra = extra.min(MENU_MAX_EXTRA);
    format!(
        "for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"{title}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           {top_js}\
           if (panel < 24) continue;\n\
           w.frameGeometry = {{x: full.x, y: top - {extra}, width: full.width, height: panel + {extra}}};\n\
         }}\n",
        title = STRIP_TITLE,
        top_js = PANEL_TOP_JS
    )
}

/// Taskbar menus: grow the window upward by `extra` px so a menu can open
/// above the bar (the rest of that area is transparent), or shrink back
/// with 0. Only while docked in school mode.
#[tauri::command]
pub fn school_menu(extra: f64) -> Result<(), String> {
    if !is_school() || crate::orb::is_orb() {
        return Ok(());
    }
    let extra = if extra.is_finite() { extra.max(0.0) as u32 } else { 0 };
    std::thread::spawn(move || run_kwin_named(&menu_script(extra), "jarvis_school_menu"));
    Ok(())
}

/// Window properties + dock for the strip. Also used by show_overlay.
pub fn apply(app: &AppHandle) {
    ensure_rule();
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        let _ = win.set_title(STRIP_TITLE);
        // tauri.conf.json gives the HUD a 960x540 minimum; the strip is tiny.
        let _ = win.set_min_size(None::<tauri::Size>);
        let _ = win.set_decorations(false);
        let _ = win.set_skip_taskbar(true);
        let _ = win.set_always_on_top(true);
        let _ = win.set_focusable(false);
        let _ = win.set_size(tauri::Size::Logical(tauri::LogicalSize {
            width: STRIP_WIDTH,
            height: STRIP_HEIGHT,
        }));
        let _ = win.show();
        // A real taskbar: its app buttons take clicks (still never focus).
        let _ = win.set_ignore_cursor_events(false);
    }
    std::thread::spawn(|| {
        // Let the compositor map the resized window before docking it.
        std::thread::sleep(std::time::Duration::from_millis(400));
        let primary = primary_output_name();
        run_kwin(&kwin_script(true, None, primary.as_deref()));
    });
}

/// True while the return transition plays (school mode stays on until its
/// "restore" stage, so the stages keep working).
static RETURNING: AtomicBool = AtomicBool::new(false);

/// Back to the normal HUD, playing the return transition when the bar is
/// up: the page (school-return.tsx) drafts the HUD's outline where it will
/// land and scans it in, calling "cover-at" then "restore". If it stalls,
/// [`TX_WATCHDOG_MS`] restores directly.
pub fn exit(app: &AppHandle) {
    if !is_school() || !overlay_visible(app) {
        restore(app);
        return;
    }
    let gen = TX_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    RETURNING.store(true, Ordering::SeqCst);
    signal(app, "return");
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(TX_WATCHDOG_MS));
        if TX_GEN.load(Ordering::SeqCst) == gen && RETURNING.swap(false, Ordering::SeqCst) {
            eprintln!("jarvis: school return stalled; restoring directly");
            restore(&app);
        }
    });
}

/// Back to the normal HUD at its saved geometry, immediately.
fn restore(app: &AppHandle) {
    restore_at(app, None, 400);
}

/// Back to the normal HUD. `frame` (the HUD window's frame, title bar
/// included) is where it lands, and is saved as its geometry; None uses
/// the saved one. KWin places it `settle_ms` after the decorations come
/// back, so GTK's title bar is already there.
fn restore_at(app: &AppHandle, frame: Option<crate::overlay::OverlayGeometry>, settle_ms: u64) {
    SCHOOL.store(false, Ordering::SeqCst);
    RETURNING.store(false, Ordering::SeqCst);
    let home = crate::env_cfg::jarvis_home();
    let path = crate::overlay::geometry_path(&home);
    if let Some(f) = frame.as_ref() {
        if let Err(e) = crate::overlay::save_geometry(&path, f) {
            eprintln!("jarvis: overlay geometry save failed ({e})");
        }
    }
    let placed = frame.is_some();
    let geom = frame.or_else(|| crate::overlay::load_geometry(&path));
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        let _ = win.set_ignore_cursor_events(crate::commands::click_through_desired());
        let _ = win.set_title(HUD_TITLE);
        let _ = win.set_min_size(Some(tauri::Size::Logical(tauri::LogicalSize {
            width: 960.0,
            height: 540.0,
        })));
        let _ = win.set_focusable(true);
        let _ = win.set_decorations(true);
        let _ = win.set_skip_taskbar(false);
        let (w, h) = geom
            .as_ref()
            .map(|g| (g.width, g.height))
            .unwrap_or((crate::overlay::OVERLAY_WIDTH, crate::overlay::OVERLAY_HEIGHT));
        // A placed return goes straight to its frame via KWin; resizing
        // here first would flash the HUD at the wrong size.
        if !placed {
            let _ = win.set_size(tauri::Size::Physical(tauri::PhysicalSize { width: w, height: h }));
        }
    }
    let restore = geom.map(|g| (g.x, g.y, g.width, g.height));
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(settle_ms));
        run_kwin(&kwin_script(false, restore, None));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn disk_mode_parses() {
        let tmp = std::env::temp_dir().join("jarvis-school-test");
        std::fs::create_dir_all(&tmp).ok();
        std::fs::write(tmp.join("mode.json"), r#"{"mode": "school", "since": 1}"#).ok();
        assert!(school_on_disk(&tmp));
        std::fs::write(tmp.join("mode.json"), r#"{"mode": "normal"}"#).ok();
        assert!(!school_on_disk(&tmp));
        std::fs::write(tmp.join("mode.json"), "{broken").ok();
        assert!(!school_on_disk(&tmp));
        std::fs::remove_dir_all(&tmp).ok();
        assert!(!school_on_disk(&tmp));
    }

    #[test]
    fn scripts_target_only_our_window() {
        let dock = kwin_script(true, None, Some("DP-1"));
        assert!(dock.contains("w.caption !== \"Jarvis\""));
        assert!(dock.contains("KWin.MaximizeArea") && dock.contains("keepAbove = true"));
        let back = kwin_script(false, Some((10, 20, 1280, 720)), None);
        assert!(back.contains("x: 10, y: 20, width: 1280, height: 720"));
        assert!(back.contains("skipTaskbar = false"));
        assert!(!kwin_script(false, None, None).contains("frameGeometry ="));
        assert!(dock.contains(STRIP_TITLE));
        // Covers the whole bottom panel, falling back to bottom-right.
        assert!(dock.contains("KWin.FullScreenArea") && dock.contains("panel >= 24"));
        assert!(dock.contains("width: full.width, height: panel"));
        // Docks on the primary display, else the screen Sir is working on.
        assert!(dock.contains("o.name === \"DP-1\") || workspace.activeScreen"));
        assert!(dock.contains("sendClientToScreen(w, target)"));
    }

    #[test]
    fn primary_output_is_the_priority_one_screen() {
        let json = r#"{"outputs": [
            {"name": "DP-1", "priority": 2, "enabled": true},
            {"name": "HDMI-A-2", "priority": 1, "enabled": true},
            {"name": "eDP-1", "priority": 3, "enabled": true}]}"#;
        assert_eq!(parse_primary_output(json).as_deref(), Some("HDMI-A-2"));
        // Disabled outputs and unsafe names never reach the script.
        assert_eq!(parse_primary_output(r#"{"outputs":[{"name":"DP-1","priority":1,"enabled":false}]}"#), None);
        assert_eq!(parse_primary_output(r#"{"outputs":[{"name":"a\"b","priority":1}]}"#), None);
        assert_eq!(parse_primary_output("not json"), None);
    }

    #[test]
    fn menu_script_grows_upward_and_clamps() {
        let s = menu_script(420);
        assert!(s.contains(STRIP_TITLE) && s.contains("resourceClass"));
        assert!(s.contains("y: top - 420") && s.contains("height: panel + 420"));
        assert!(s.contains("d.dock") && s.contains("d.output !== w.output"));
        assert!(menu_script(0).contains("height: panel + 0"));
        assert!(menu_script(99_999).contains(&format!("panel + {MENU_MAX_EXTRA}")));
    }

    #[test]
    fn measure_script_reports_to_bridge_with_clean_nonce() {
        let s = measure_script("ab12\"; evil()", Some("eDP-1"));
        assert!(s.contains("callDBus(\"org.jarvis.Focus\"") && s.contains("\"SchoolGeom\""));
        assert!(s.contains("nonce: \"ab12evil\"") && !s.contains("evil()"));
        assert!(s.contains("o.name === \"eDP-1\"") && s.contains("w.clientGeometry"));
        assert!(s.contains("panel: Math.round"));
    }

    #[test]
    fn cover_script_fills_current_or_primary_screen() {
        let here = cover_script(Cover::Here, Some("eDP-1"), None);
        assert!(here.contains("const target = w.output;") && !here.contains("eDP-1"));
        let prim = cover_script(Cover::Primary, Some("eDP-1"), None);
        assert!(prim.contains("o.name === \"eDP-1\"") && prim.contains("sendClientToScreen"));
        assert!(prim.contains("width: g.width, height: g.height") && prim.contains("noBorder = true"));
        // Unframing keeps the content rect: the HUD folds in place.
        let un = cover_script(Cover::Unframe, None, None);
        assert!(un.contains("const c = w.clientGeometry;") && un.contains("width: c.width"));
        assert!(!un.contains("sendClientToScreen"));
        // The return covers whichever screen holds the HUD's centre.
        let at = cover_script(Cover::At([2000, 100, 1280, 720]), None, None);
        assert!(at.contains("2640 >= g.x") && at.contains("460 < g.y + g.height"));
        // With the page's content rect, it pins exactly there.
        let pinned = cover_script(Cover::Unframe, None, Some([160, 102, 1280, 683]));
        assert!(pinned.contains("{x: 160, y: 102, width: 1280, height: 683}"));
    }

    #[test]
    fn menu_click_js_maps_names_to_bar_buttons() {
        assert!(menu_click_js("launcher").contains(".sbar-start"));
        assert!(menu_click_js("quick").contains(".sbar-tray"));
        assert!(menu_click_js("calendar").contains(".sbar-clock"));
        assert!(menu_click_js("\"); evil()").contains("data-open"));
    }

    #[test]
    fn signal_js_only_sends_known_phases() {
        assert!(signal_js("collapse").contains("detail: 'collapse'"));
        assert!(signal_js("expand").contains("detail: 'expand'"));
        assert!(signal_js("arrive").contains("detail: 'arrive'"));
        assert!(signal_js("return").contains("detail: 'return'"));
        assert!(signal_js("');alert(1);//").contains("detail: 'collapse'"));
    }

    #[test]
    fn rule_is_exact_title_and_forced() {
        let rule = rule_entries();
        assert!(rule.contains(&("title", STRIP_TITLE)) && rule.contains(&("titlematch", "1")));
        assert!(rule.contains(&("acceptfocus", "false")) && rule.contains(&("acceptfocusrule", "2")));
        assert_eq!(rules_with_ours(""), RULE_ID);
        assert_eq!(rules_with_ours("abc"), format!("abc,{RULE_ID}"));
        assert_eq!(rules_with_ours(&format!("abc,{RULE_ID}")), format!("abc,{RULE_ID}"));
    }
}
