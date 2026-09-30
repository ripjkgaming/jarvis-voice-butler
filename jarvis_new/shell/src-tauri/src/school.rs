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
/// Also `inset`/`barH`: see [`PANEL_INSET_JS`].
/// JS for `inset`/`barH` given `panel`, `top`, `full` and `jkOut` (the
/// output) in scope. A floating Plasma panel's window is its thickness
/// plus a gap on every side (a 40 px bar in a 56 px window): the bar covers
/// only the panel as drawn, so it is the real taskbar's size. But Plasma
/// de-floats the panel (flush, the whole window) while a window touches
/// it, e.g. anything maximized; then the bar is flush too, or the real
/// panel would show around it. `JK_T` (the thickness, from Plasma) is
/// prepended by [`with_panel_thickness`]; unknown -> no inset.
macro_rules! inset_js {
    () => {
        "const jkT = (typeof JK_T === \"number\") ? JK_T : 0;\n\
    let jkTouch = false;\n\
    for (const v of workspace.windowList()) {\n\
      if (!v.normalWindow || v.minimized || v.dock || v.output !== jkOut) continue;\n\
      if (String(v.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
      if (!v.onAllDesktops && !v.desktops.includes(workspace.currentDesktop)) continue;\n\
      const f = v.frameGeometry;\n\
      if (f.y + f.height >= top - 1 && f.x < full.x + full.width && f.x + f.width > full.x) { jkTouch = true; break; }\n\
    }\n\
    const inset = (!jkTouch && jkT > 0 && panel > jkT + 1 && panel - jkT <= 48) ? Math.floor((panel - jkT) / 2) : 0;\n\
    const barH = panel - 2 * inset;\n"
    };
}

/// JS computing `top`/`panel` for window `w`: the bottom panel's real top
/// edge. The reserved strip (full minus maximize area) can be shorter than
/// the panel as drawn (floating panels overhang it by a few px, which then
/// peek above the bar), so the tallest bottom dock on the same output wins.
/// Also `inset`/`barH`: see [`inset_js`].
const PANEL_TOP_JS: &str = concat!(
    "const full = workspace.clientArea(KWin.FullScreenArea, w);\n\
    const a = workspace.clientArea(KWin.MaximizeArea, w);\n\
    let top = a.y + a.height;\n\
    for (const d of workspace.windowList()) {\n\
      if (!d.dock || d.output !== w.output) continue;\n\
      const g = d.frameGeometry;\n\
      if (g.y > full.y + full.height / 2 && g.y < top) top = g.y;\n\
    }\n\
    const panel = (full.y + full.height) - top;\n\
    const jkOut = w.output;\n",
    inset_js!()
);

/// [`inset_js`] for the scripts that compute `panel` their own way.
const PANEL_INSET_JS: &str = inset_js!();

/// Thickness (px) of the primary screen's bottom Plasma panel as drawn
/// (Plasma's screen 0 is the primary), or 0 when unknown / not Plasma.
pub fn primary_panel_thickness() -> u32 {
    let js = "print(panels().filter(function(p){return p.screen==0 && p.location=='bottom';})\
              .map(function(p){return p.height;}).join(','))";
    std::process::Command::new("dbus-send")
        .args([
            "--session",
            "--print-reply",
            "--dest=org.kde.plasmashell",
            "/PlasmaShell",
            "org.kde.PlasmaShell.evaluateScript",
            &format!("string:{js}"),
        ])
        .output()
        .ok()
        .map(|o| parse_thickness(&String::from_utf8_lossy(&o.stdout)))
        .unwrap_or(0)
}

/// First panel height in a `dbus-send --print-reply` of evaluateScript
/// (`string "40"`), 0 if none. Pure.
pub fn parse_thickness(reply: &str) -> u32 {
    reply
        .split('"')
        .nth(1)
        .and_then(|s| s.split(',').next())
        .and_then(|s| s.trim().parse::<u32>().ok())
        .filter(|t| (16..=200).contains(t))
        .unwrap_or(0)
}

/// Prefix a school script with the primary panel's thickness (`JK_T`).
fn with_panel_thickness(script: &str) -> String {
    format!("const JK_T = {};\n{script}", primary_panel_thickness())
}

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
               w.frameGeometry = {{x: full.x + inset, y: top + inset, width: full.width - 2 * inset, height: barH}};\n\
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
            "w.noBorder = false; w.keepAbove = false;\n{geo}\n\
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

/// KWin script name of the dock keeper (stays loaded while docked).
const KEEPER_NAME: &str = "jarvis_school_keeper";

/// Menu space the shell granted the strip above the panel (px, 0 = none),
/// set by [`school_menu`]. The keeper sizes to exactly panel + this: it
/// used to *guess* "tall = menu open", so right after a transition it
/// caught the still-full-size window, took it for a menu and pinned it at
/// panel + 900 px, and the page (tall window = HUD) came back as the full
/// HUD in school mode.
static MENU_EXTRA_NOW: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

/// Persistent KWin script that keeps the docked strip exactly over the
/// bottom panel. The one-shot dock only runs on entry, so a monitor being
/// plugged/unplugged, a layout or scale change, or the panel moving left
/// the strip wherever KWin shoved it (e.g. 40 px above the panel after
/// dropping to the laptop screen). This re-docks on every such change,
/// debounced, at exactly panel + `menu_extra` tall (the menu space the
/// shell granted; never inferred from the window's size). Target: the
/// `primary` connector when present, else the first screen with a bottom
/// panel, else the active screen. Pure.
pub fn keeper_script(primary: Option<&str>, menu_extra: u32) -> String {
    format!(
        "const JK_PRIMARY = \"{primary}\";\n\
         const JK_TITLE = \"{title}\";\n\
         function jkIsStrip(w) {{\n\
           return w && w.caption === JK_TITLE && String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\");\n\
         }}\n\
         function jkStrip() {{\n\
           for (const w of workspace.windowList()) if (jkIsStrip(w)) return w;\n\
           return null;\n\
         }}\n\
         function jkPanel(o) {{\n\
           const full = workspace.clientArea(KWin.FullScreenArea, o, workspace.currentDesktop);\n\
           const a = workspace.clientArea(KWin.MaximizeArea, o, workspace.currentDesktop);\n\
           let top = a.y + a.height;\n\
           for (const d of workspace.windowList()) {{\n\
             if (!d.dock || d.output !== o) continue;\n\
             const g = d.frameGeometry;\n\
             if (g.y > full.y + full.height / 2 && g.y < top) top = g.y;\n\
           }}\n\
           const panel = (full.y + full.height) - top;\n\
           const jkOut = o;\n\
           {inset_js}\
           return {{full: full, a: a, panel: panel, inset: inset, barH: barH}};\n\
         }}\n\
         function jkTarget() {{\n\
           const screens = workspace.screens;\n\
           for (const o of screens) if (o.name === JK_PRIMARY) return o;\n\
           for (const o of screens) if (jkPanel(o).panel >= 24) return o;\n\
           return workspace.activeScreen || screens[0];\n\
         }}\n\
         let jkBusy = false;\n\
         function jkDock() {{\n\
           if (jkBusy) return;\n\
           const w = jkStrip();\n\
           if (!w || w.minimized) return;\n\
           const o = jkTarget();\n\
           if (!o) return;\n\
           jkBusy = true;\n\
           try {{\n\
             if (w.output !== o) workspace.sendClientToScreen(w, o);\n\
             const p = jkPanel(o);\n\
             const g = w.frameGeometry;\n\
             let want;\n\
             if (p.panel >= 24) {{\n\
               const h = p.barH + {menu_extra};\n\
               want = {{x: p.full.x + p.inset, y: p.full.y + p.full.height - p.inset - h, width: p.full.width - 2 * p.inset, height: h}};\n\
             }} else {{\n\
               want = {{x: p.a.x + p.a.width - g.width - {m}, y: p.a.y + p.a.height - g.height - {m}, width: g.width, height: g.height}};\n\
             }}\n\
             const off = Math.abs(g.x - want.x) > 1 || Math.abs(g.y - want.y) > 1\n\
               || Math.abs(g.width - want.width) > 1 || Math.abs(g.height - want.height) > 1;\n\
             if (off) w.frameGeometry = want;\n\
             if (!w.keepAbove) w.keepAbove = true;\n\
           }} catch (e) {{}}\n\
           jkBusy = false;\n\
           jkRaise();\n\
         }}\n\
         function jkRaise() {{\n\
           const w = jkStrip();\n\
           if (!w || w.minimized) return;\n\
           try {{\n\
             const order = workspace.stackingOrder;\n\
             const mine = order.indexOf(w);\n\
             for (let i = mine + 1; i < order.length; i++) {{\n\
               const d = order[i];\n\
               if (d && d.dock && d.output === w.output) {{ workspace.raiseWindow(w); return; }}\n\
             }}\n\
           }} catch (e) {{}}\n\
         }}\n\
         let jkTimer = null;\n\
         try {{\n\
           jkTimer = new QTimer();\n\
           jkTimer.singleShot = true;\n\
           jkTimer.interval = 250;\n\
           jkTimer.timeout.connect(jkDock);\n\
         }} catch (e) {{ jkTimer = null; }}\n\
         function jkSoon() {{ if (jkTimer) jkTimer.start(); else jkDock(); }}\n\
         function jkHook(w) {{\n\
           if (!w) return;\n\
           const jarvis = String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\");\n\
           // Normal windows too: one touching the panel de-floats it.\n\
           if (!jarvis && !w.dock && !w.normalWindow) return;\n\
           try {{ w.frameGeometryChanged.connect(jkSoon); }} catch (e) {{}}\n\
           try {{ w.outputChanged.connect(jkSoon); }} catch (e) {{}}\n\
           try {{ w.minimizedChanged.connect(jkSoon); }} catch (e) {{}}\n\
           try {{ w.desktopsChanged.connect(jkSoon); }} catch (e) {{}}\n\
           if (jarvis) {{\n\
             try {{ w.captionChanged.connect(jkSoon); }} catch (e) {{}}\n\
           }}\n\
         }}\n\
         try {{ workspace.screensChanged.connect(jkSoon); }} catch (e) {{}}\n\
         try {{ workspace.stackingOrderChanged.connect(jkRaise); }} catch (e) {{}}\n\
         try {{ workspace.virtualScreenGeometryChanged.connect(jkSoon); }} catch (e) {{}}\n\
         try {{ workspace.windowAdded.connect(function(w) {{ jkHook(w); jkSoon(); }}); }} catch (e) {{}}\n\
         try {{ workspace.windowRemoved.connect(function(w) {{ jkSoon(); }}); }} catch (e) {{}}\n\
         try {{ workspace.currentDesktopChanged.connect(jkSoon); }} catch (e) {{}}\n\
         for (const w of workspace.windowList()) jkHook(w);\n\
         jkDock();\n",
        primary = primary.unwrap_or(""),
        title = STRIP_TITLE,
        m = STRIP_MARGIN,
        menu_extra = menu_extra.min(MENU_MAX_EXTRA),
        inset_js = PANEL_INSET_JS,
    )
}

/// Load the dock keeper (replacing any running one). Fail-soft.
fn start_keeper() {
    std::thread::spawn(|| {
        let primary = primary_output_name();
        // Re-checked on this thread: an exit or entry that began since the
        // keeper was asked for has already stopped it, and loading it now
        // would snap the window back to bar height mid-transition.
        if !is_school() || in_transition() {
            return;
        }
        let extra = MENU_EXTRA_NOW.load(Ordering::SeqCst);
        load_kwin_named(&with_panel_thickness(&keeper_script(primary.as_deref(), extra)), KEEPER_NAME);
    });
}

/// Unload the dock keeper, so it never fights a transition or the HUD.
fn stop_keeper() {
    unload_kwin_named(KEEPER_NAME);
}

fn run_kwin(script: &str) {
    run_kwin_named(script, "jarvis_school_dock");
}

/// KWin script filling the work area (screen minus panels) with the HUD,
/// keeping its title bar so the top still drags it. Fills a matching window
/// now and once more when one is mapped later (boot may show it late).
fn boot_fill_script() -> String {
    format!(
        "function jkFill(w) {{\n\
           if (!w || w.caption !== \"{hud}\") return;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) return;\n\
           const a = workspace.clientArea(KWin.MaximizeArea, w);\n\
           w.frameGeometry = {{x: a.x, y: a.y, width: a.width, height: a.height}};\n\
         }}\n\
         for (const w of workspace.windowList()) jkFill(w);\n\
         workspace.windowAdded.connect(jkFill);\n",
        hud = HUD_TITLE
    )
}

/// At launch (normal mode) the HUD fills the screen's work area but stays a
/// normal decorated window: drag the title bar to move it. Fail-soft.
pub fn fill_work_area_at_boot() {
    if is_school() {
        return;
    }
    std::thread::spawn(|| {
        const NAME: &str = "jarvis_boot_fill";
        if load_kwin_named(&boot_fill_script(), NAME) {
            std::thread::sleep(std::time::Duration::from_secs(20));
        }
        unload_kwin_named(NAME);
    });
}

/// Load + run + unload a KWin script over D-Bus. Fail-soft (X11 / no KWin).
/// Shared with the Brave orb (orb.rs).
pub(crate) fn run_kwin_named(script: &str, name: &str) {
    if load_kwin_named(script, name) {
        std::thread::sleep(std::time::Duration::from_millis(300));
    }
    unload_kwin_named(name);
}

fn kwin_dbus(args: &[&str]) -> std::io::Result<std::process::Output> {
    std::process::Command::new("dbus-send")
        .args(["--session", "--print-reply", "--dest=org.kde.KWin"])
        .args(args)
        .output()
}

/// Unload a KWin script by name. Fail-soft.
fn unload_kwin_named(name: &str) {
    let _ = kwin_dbus(&["/Scripting", "org.kde.kwin.Scripting.unloadScript", &format!("string:{name}")]);
}

/// Load (replacing a same-named one) and run a KWin script, leaving it
/// loaded. True when it started. Fail-soft (X11 / no KWin).
fn load_kwin_named(script: &str, name: &str) -> bool {
    let path = std::env::temp_dir().join(format!("{name}.js"));
    if std::fs::write(&path, script).is_err() {
        return false;
    }
    unload_kwin_named(name);
    let out = match kwin_dbus(&[
        "/Scripting",
        "org.kde.kwin.Scripting.loadScript",
        &format!("string:{}", path.display()),
        &format!("string:{name}"),
    ]) {
        Ok(o) => String::from_utf8_lossy(&o.stdout).to_string(),
        Err(_) => return false,
    };
    let id = out
        .split_whitespace()
        .skip_while(|t| *t != "int32")
        .nth(1)
        .and_then(|t| t.parse::<i64>().ok());
    match id {
        Some(id) if id >= 0 => {
            let _ = kwin_dbus(&[&format!("/Scripting/Script{id}"), "org.kde.kwin.Script.run"]);
            true
        }
        _ => false,
    }
}

/// Turn the overlay into the strip, playing the entry transition.
///
/// The page drives the transition (school-transition.tsx) and calls back
/// through [`school_stage`] for each window change: HUD visible ->
/// "collapse" (it folds into the screen sides first), hidden -> "arrive"
/// (tracers only). [`TX_WATCHDOG_MS`] docks it anyway if the page stalls.
pub fn enter(app: &AppHandle) {
    let was = SCHOOL.swap(true, Ordering::SeqCst);
    // Entered again mid-return: drop the return (its "restore" stage is
    // ignored from here on) and play the arrival from wherever we are.
    let returning = RETURNING.swap(false, Ordering::SeqCst);
    if was && !returning {
        apply(app);
        return;
    }
    stop_keeper();
    MENU_EXTRA_NOW.store(0, Ordering::SeqCst);
    ENTERING.store(true, Ordering::SeqCst);
    let gen = TX_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    let phase = if returning || !overlay_visible(app) { "arrive" } else { "collapse" };
    signal(app, phase);
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(TX_WATCHDOG_MS));
        if is_school() && TX_GEN.load(Ordering::SeqCst) == gen && DOCKED_GEN.load(Ordering::SeqCst) != gen {
            eprintln!("jarvis: school transition stalled; docking directly");
            apply(&app);
        }
    });
}

/// True while an entry transition plays (until its "dock" stage). An exit
/// in that window skips the return animation: two transitions fighting
/// over the window left it fullscreen and never docked.
static ENTERING: AtomicBool = AtomicBool::new(false);

/// An entry or return animation owns the window right now.
fn in_transition() -> bool {
    ENTERING.load(Ordering::SeqCst) || RETURNING.load(Ordering::SeqCst)
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
/// If the page hasn't docked the strip by now (ms), the shell does. The
/// page's animation clock pauses while the webview stalls (window moves
/// under load), so a healthy run can take well over its nominal ~8 s.
pub const TX_WATCHDOG_MS: u64 = 20_000;

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
           const panel = (full.y + full.height) - top;\n\
           const jkOut = prim;\n\
           {inset_js}\
           const dx = (pg.x + pg.width / 2) - (og.x + og.width / 2);\n\
           const dy = (pg.y + pg.height / 2) - (og.y + og.height / 2);\n\
           const dir = out === prim ? null : Math.abs(dx) >= Math.abs(dy) ? (dx < 0 ? \"left\" : \"right\") : (dy < 0 ? \"up\" : \"down\");\n\
           const r = q => ({{x: Math.round(q.x), y: Math.round(q.y), w: Math.round(q.width), h: Math.round(q.height)}});\n\
           const hud = r(g); hud.x -= Math.round(og.x); hud.y -= Math.round(og.y);\n\
           callDBus(\"org.jarvis.Focus\", \"/org/jarvis/Focus\", \"org.jarvis.Focus\", \"SchoolGeom\", JSON.stringify({{\n\
             nonce: \"{nonce}\", visible: !w.minimized, hud: hud, out: r(og), primary: r(pg),\n\
             same: out === prim, dir: dir, panel: Math.round(barH), inset: inset\n\
           }}));\n\
           break;\n\
         }}\n",
        title = STRIP_TITLE,
        primary = primary.unwrap_or(""),
        inset_js = PANEL_INSET_JS,
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
        crate::commands::click_through_if_visible(&win, ghost);
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
                run_kwin_named(
                    &with_panel_thickness(&measure_script(&nonce, primary.as_deref())),
                    "jarvis_school_measure",
                );
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
            // Show first: click-through on a hidden (unrealized) window
            // panics tao and takes the whole shell down.
            if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
                let _ = win.show();
            }
            strip_window_props(&app, true);
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
            ENTERING.store(false, Ordering::SeqCst);
            ensure_rule();
            strip_window_props(&app, false);
            std::thread::spawn(|| {
                let primary = primary_output_name();
                run_kwin(&with_panel_thickness(&kwin_script(true, None, primary.as_deref())));
            });
            start_keeper();
        }
        "restore" => {
            // Stale: the return was cancelled by a re-entry.
            if !RETURNING.swap(false, Ordering::SeqCst) {
                return Ok(());
            }
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
           w.frameGeometry = {{x: full.x + inset, y: top + inset - {extra}, width: full.width - 2 * inset, height: barH + {extra}}};\n\
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
    // Mid-transition the window belongs to the animation: a menu closing as
    // the bar leaves must not resize it or re-arm the keeper.
    if !is_school() || crate::orb::is_orb() || in_transition() {
        return Ok(());
    }
    let extra = if extra.is_finite() { extra.max(0.0) as u32 } else { 0 };
    MENU_EXTRA_NOW.store(extra.min(MENU_MAX_EXTRA), Ordering::SeqCst);
    std::thread::spawn(move || {
        run_kwin_named(&with_panel_thickness(&menu_script(extra)), "jarvis_school_menu")
    });
    // Re-arm the keeper at the new height, or it would undo the menu.
    start_keeper();
    Ok(())
}

/// Window properties + dock for the strip. Also used by show_overlay.
pub fn apply(app: &AppHandle) {
    ENTERING.store(false, Ordering::SeqCst);
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
        crate::commands::click_through_if_visible(&win, false);
    }
    std::thread::spawn(|| {
        // Let the compositor map the resized window before docking it.
        std::thread::sleep(std::time::Duration::from_millis(400));
        let primary = primary_output_name();
        run_kwin(&with_panel_thickness(&kwin_script(true, None, primary.as_deref())));
    });
    start_keeper();
}

/// True while the return transition plays (school mode stays on until its
/// "restore" stage, so the stages keep working).
static RETURNING: AtomicBool = AtomicBool::new(false);

/// Back to the normal HUD, playing the return transition when the bar is
/// up: the page (school-return.tsx) drafts the HUD's outline where it will
/// land and scans it in, calling "cover-at" then "restore". If it stalls,
/// [`TX_WATCHDOG_MS`] restores directly.
pub fn exit(app: &AppHandle) {
    stop_keeper();
    MENU_EXTRA_NOW.store(0, Ordering::SeqCst);
    if !is_school() || !overlay_visible(app) {
        restore(app);
        return;
    }
    if ENTERING.swap(false, Ordering::SeqCst) {
        // Mid-entry: cancel the page's transition and go straight back.
        TX_GEN.fetch_add(1, Ordering::SeqCst);
        signal(app, "expand");
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
    stop_keeper();
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
        crate::commands::click_through_if_visible(&win, crate::commands::click_through_desired());
        let _ = win.set_title(HUD_TITLE);
        let _ = win.set_min_size(Some(tauri::Size::Logical(tauri::LogicalSize {
            width: 960.0,
            height: 540.0,
        })));
        let _ = win.set_focusable(true);
        let _ = win.set_always_on_top(false);
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
        assert!(dock.contains("width: full.width - 2 * inset, height: barH"));
        // A floating panel's gap is trimmed off (bar = the panel as drawn).
        assert!(dock.contains("Math.floor((panel - jkT) / 2)"));
        // ...except while a window touches it: Plasma de-floats it flush.
        assert!(dock.contains("jkTouch = true") && dock.contains("!jkTouch && jkT > 0"));
        assert!(keeper_script(None, 0).contains("const jkOut = o;"));
        assert!(measure_script("n", None).contains("const jkOut = prim;"));
        assert!(with_panel_thickness("x").ends_with("\nx"));
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
        assert!(s.contains("y: top + inset - 420") && s.contains("height: barH + 420"));
        assert!(s.contains("d.dock") && s.contains("d.output !== w.output"));
        assert!(menu_script(0).contains("height: barH + 0"));
        assert!(menu_script(99_999).contains(&format!("barH + {MENU_MAX_EXTRA}")));
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
    fn panel_thickness_parses_plasma_reply() {
        let reply = "method return time=1 sender=:1.4 -> destination=:1.9 serial=7 reply_serial=2\n   string \"40\"\n";
        assert_eq!(parse_thickness(reply), 40);
        assert_eq!(parse_thickness("   string \"50,30\"\n"), 50);
        assert_eq!(parse_thickness("   string \"\"\n"), 0);
        assert_eq!(parse_thickness("Error org.freedesktop.DBus.Error.ServiceUnknown"), 0);
        assert_eq!(parse_thickness("   string \"4000\"\n"), 0);
    }

    #[test]
    fn keeper_redocks_on_screen_changes_without_eating_menus() {
        let k = keeper_script(Some("HDMI-A-2"), 0);
        assert!(k.contains("o.name === JK_PRIMARY") && k.contains("\"HDMI-A-2\""));
        assert!(k.contains(STRIP_TITLE) && k.contains("resourceClass"));
        assert!(k.contains("workspace.screensChanged.connect(jkSoon)"));
        assert!(k.contains("virtualScreenGeometryChanged") && k.contains("outputChanged"));
        assert!(k.contains("sendClientToScreen(w, o)"));
        // Never left stacked under the panel it covers.
        assert!(k.contains("stackingOrderChanged.connect(jkRaise)") && k.contains("workspace.raiseWindow(w)"));
        // Bottom edge pinned to the screen bottom, full width.
        assert!(k.contains("y: p.full.y + p.full.height - p.inset - h, width: p.full.width - 2 * p.inset"));
        // Exactly the panel with no menu open: never guesses from the
        // window's own height (a still-full-size window after a transition
        // was taken for a menu and pinned tall, showing the HUD).
        assert!(k.contains("const h = p.barH + 0;"));
        assert!(!k.contains("g.height - p.panel"));
        // A granted menu height is kept, capped.
        assert!(keeper_script(None, 480).contains("const h = p.barH + 480;"));
        assert!(keeper_script(None, 5000).contains(&format!("const h = p.barH + {MENU_MAX_EXTRA};")));
        // Falls back to any screen with a panel when the primary is gone.
        assert!(keeper_script(None, 0).contains("jkPanel(o).panel >= 24"));
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
