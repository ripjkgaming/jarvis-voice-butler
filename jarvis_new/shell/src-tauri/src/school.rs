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

/// Serialize mode/generation changes with their native effects. Atomic
/// checks alone leave a gap in which an old cleanup can delete a new rule
/// or move a newly entered strip. Window work under this gate must run on
/// the main thread: a background Tauri getter waits for that same thread.
#[derive(Default)]
struct LifecycleGate(std::sync::Mutex<()>);

impl LifecycleGate {
    const fn new() -> Self {
        Self(std::sync::Mutex::new(()))
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, ()> {
        self.0.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn run_if(&self, current: impl FnOnce() -> bool, effect: impl FnOnce()) -> bool {
        let _lock = self.lock();
        if !current() {
            return false;
        }
        effect();
        true
    }

    fn run_for_mode(
        &self,
        school: &AtomicBool,
        generation: &AtomicU64,
        expected_school: bool,
        expected_gen: u64,
        effect: impl FnOnce(),
    ) -> bool {
        self.run_if(
            || school.load(Ordering::SeqCst) == expected_school
                && generation.load(Ordering::SeqCst) == expected_gen,
            effect,
        )
    }

    fn start_one_shot(
        &self,
        name: &str,
        current: impl FnOnce() -> bool,
        start: impl FnOnce(&str) -> bool,
    ) -> Option<OneShotScript> {
        let _lock = self.lock();
        if !current() {
            return None;
        }
        let serial = ONE_SHOT_SERIAL.fetch_add(1, Ordering::Relaxed);
        let name = format!("{name}_{}_{serial}", std::process::id());
        let loaded = start(&name);
        Some(OneShotScript { name, loaded })
    }
}

static LIFECYCLE: LifecycleGate = LifecycleGate::new();
static ONE_SHOT_SERIAL: AtomicU64 = AtomicU64::new(0);

/// A one-shot owns its script and temporary file, even while a newer
/// invocation with the same purpose is running. Retention and cleanup
/// happen after start_one_shot releases the lifecycle gate.
struct OneShotScript {
    name: String,
    loaded: bool,
}

impl OneShotScript {
    fn finish_with(self, retain: impl FnOnce(), cleanup: impl FnOnce(&str)) {
        if self.loaded {
            retain();
        }
        cleanup(&self.name);
    }

    fn finish(self) {
        self.finish_with(
            || std::thread::sleep(std::time::Duration::from_millis(300)),
            |name| {
                unload_kwin_named(name);
                let _ = std::fs::remove_file(std::env::temp_dir().join(format!("{name}.js")));
            },
        );
    }
}

fn current_mode(school: bool, gen: u64) -> bool {
    is_school() == school && TX_GEN.load(Ordering::SeqCst) == gen
}

fn run_in_mode(school: bool, gen: u64, effect: impl FnOnce()) -> bool {
    LIFECYCLE.run_for_mode(&SCHOOL, &TX_GEN, school, gen, effect)
}

/// Public entry points also arrive on the single-instance D-Bus worker.
/// Wry runs this inline when already on main; workers wait without holding
/// the gate. Take the gate on main, preserving synchronous completion.
fn on_main_sync<T: Send + 'static>(
    app: &AppHandle,
    action: impl FnOnce(&AppHandle) -> T + Send + 'static,
) -> Option<T> {
    let handle = app.clone();
    let (tx, rx) = std::sync::mpsc::channel();
    // A timed-out call must not start later when a stalled event loop wakes.
    let claimed = std::sync::Arc::new(AtomicBool::new(false));
    let on_main_claimed = claimed.clone();
    if let Err(error) = app.run_on_main_thread(move || {
        let _lifecycle = LIFECYCLE.lock();
        if !on_main_claimed.swap(true, Ordering::SeqCst) {
            let _ = tx.send(action(&handle));
        }
    }) {
        eprintln!("jarvis: school main-thread dispatch failed ({error})");
        return None;
    }
    match rx.recv_timeout(std::time::Duration::from_secs(30)) {
        Ok(value) => Some(value),
        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
            let started = claimed.swap(true, Ordering::SeqCst);
            eprintln!("jarvis: school main-thread call timed out (started={started})");
            None
        }
        Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => None,
    }
}

#[derive(Default)]
struct SuitFocusState {
    epoch: u64,
    next_lease: u64,
    active: Option<(u64, u64)>, // transition generation, modal lease
}

#[derive(Debug, PartialEq, Eq)]
enum SuitFocusChange {
    Unchanged,
    Enable(u64),
    Disable,
}

#[derive(Debug, PartialEq, Eq)]
struct SchoolApplyPlan {
    preserve_geometry: bool,
    focusable: bool,
    menu_extra: u32,
}

impl SuitFocusState {
    fn prepare_apply(&mut self, generation: u64, transitioning: bool, menu_extra: u32) -> SchoolApplyPlan {
        let focusable = self.active.map(|(gen, _)| gen) == Some(generation);
        if !transitioning {
            if !focusable && self.active.is_some() {
                self.reset();
            }
            return SchoolApplyPlan { preserve_geometry: focusable || menu_extra > 0, focusable, menu_extra };
        }
        self.reset();
        SchoolApplyPlan { preserve_geometry: false, focusable: false, menu_extra: 0 }
    }

    fn reset(&mut self) -> SuitFocusChange {
        self.epoch += 1;
        if self.active.take().is_some() {
            SuitFocusChange::Disable
        } else {
            SuitFocusChange::Unchanged
        }
    }

    fn request(
        &mut self,
        active: bool,
        lease: Option<u64>,
        generation: u64,
        epoch: u64,
        eligible: bool,
    ) -> SuitFocusChange {
        if !active {
            return if lease.is_none() || self.active == lease.map(|lease| (generation, lease)) {
                self.reset()
            } else {
                SuitFocusChange::Unchanged
            };
        }
        if !eligible || epoch != self.epoch {
            return SuitFocusChange::Unchanged;
        }
        self.next_lease += 1;
        self.active = Some((generation, self.next_lease));
        SuitFocusChange::Enable(self.next_lease)
    }
}

static SUIT_FOCUS: std::sync::Mutex<SuitFocusState> = std::sync::Mutex::new(SuitFocusState {
    epoch: 0,
    next_lease: 0,
    active: None,
});

/// Only the focus keys change; the exact-title rule keeps all its docking,
/// stacking and taskbar behavior while diagnostics temporarily takes keys.
fn suit_focus_rule_entries(active: bool) -> [(&'static str, &'static str); 2] {
    [("acceptfocus", if active { "true" } else { "false" }), ("acceptfocusrule", "2")]
}

fn set_suit_focus_locked(app: &AppHandle, active: bool) -> Result<(), String> {
    install_rule(RULE_ID, &suit_focus_rule_entries(active));
    if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
        win.set_focusable(active).map_err(|error| error.to_string())?;
        if active {
            win.set_focus().map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

/// All lifecycle/transition paths revoke modal focus before changing the
/// strip. Never make the normal HUD unfocusable when clearing an old lease.
fn reset_suit_focus_locked(app: &AppHandle) {
    let change = SUIT_FOCUS.lock().unwrap_or_else(|e| e.into_inner()).reset();
    if change == SuitFocusChange::Disable && is_school() {
        let _ = set_suit_focus_locked(app, false);
    }
}

/// Acquire keyboard access for a docked school diagnostics modal. A close
/// needs its returned lease; false/None is a page-reload reset, which the
/// frontend awaits before opening a new modal. Late acquisition replies
/// must still be closed if the modal unmounted while awaiting them.
#[tauri::command]
pub fn suit_focus(app: AppHandle, active: bool, lease: Option<u64>) -> Result<Option<u64>, String> {
    let gen = TX_GEN.load(Ordering::SeqCst);
    let epoch = SUIT_FOCUS.lock().unwrap_or_else(|e| e.into_inner()).epoch;
    on_main_sync(&app, move |app| {
        let eligible = current_mode(true, gen) && !in_transition()
            && !crate::orb::is_orb() && overlay_visible(app);
        let change = SUIT_FOCUS.lock().unwrap_or_else(|e| e.into_inner())
            .request(active, lease, gen, epoch, eligible);
        match change {
            SuitFocusChange::Enable(lease) => {
                if let Err(error) = set_suit_focus_locked(app, true) {
                    reset_suit_focus_locked(app);
                    return Err(error);
                }
                Ok(Some(lease))
            }
            SuitFocusChange::Disable => {
                if is_school() {
                    set_suit_focus_locked(app, false)?;
                }
                Ok(None)
            }
            SuitFocusChange::Unchanged => Ok(None),
        }
    }).unwrap_or_else(|| Err("suit focus main-thread dispatch failed or timed out".into()))
}

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
    "const full = workspace.clientArea(KWin.FullScreenArea, target, workspace.currentDesktop);\n\
    const a = workspace.clientArea(KWin.MaximizeArea, target, workspace.currentDesktop);\n\
    let top = a.y + a.height;\n\
    for (const d of workspace.windowList()) {\n\
      if (!d.dock || d.output !== target) continue;\n\
      const g = d.frameGeometry;\n\
      if (g.y > full.y + full.height / 2 && g.y < top) top = g.y;\n\
    }\n\
    const panel = (full.y + full.height) - top;\n\
    const jkOut = target;\n",
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

/// Read KWin's live primary on every placement, including primary changes
/// while the keeper stays loaded. The kscreen connector is a compatibility
/// fallback for older KWin; the active window and pointer are irrelevant.
const PRIMARY_OUTPUT_JS: &str = "function jkPrimary() {\n\
    const screens = workspace.screens;\n\
    const order = workspace.screenOrder || [];\n\
    for (const o of order) if (screens.includes(o)) return o;\n\
    return screens.find(o => o.name === JK_PRIMARY) || screens[0];\n\
}\n";

fn primary_script(primary: Option<&str>) -> String {
    let name = serde_json::to_string(primary.unwrap_or("")).expect("JSON string");
    format!("const JK_PRIMARY = {name};\n{PRIMARY_OUTPUT_JS}")
}

/// KWin script that docks the strip (school) or puts the HUD back.
/// Matches our overlay by exact caption "Jarvis" on a jarvis-shell window.
/// The strip docks onto the live primary; `primary` is a connector hint
/// for KWin versions without screenOrder.
pub fn kwin_script(school: bool, restore: Option<(i32, i32, u32, u32)>, primary: Option<&str>) -> String {
    let body = if school {
        format!(
            "const target = jkPrimary();\n\
             if (!target) continue;\n\
             if (w.output !== target) workspace.sendClientToScreen(w, target);\n\
             {top_js}\
             w.noBorder = true;\n\
             if (panel >= 24) {{\n\
               w.frameGeometry = {{x: full.x + inset, y: top + inset, width: full.width - 2 * inset, height: barH}};\n\
             }} else {{\n\
               const width = Math.min({width}, Math.max(1, a.width - 2 * {m}));\n\
               w.frameGeometry = {{x: a.x + a.width - width - {m}, y: a.y + a.height - {height} - {m}, width: width, height: {height}}};\n\
             }}\n\
             w.keepAbove = true; w.skipTaskbar = true; w.skipPager = true; w.skipSwitcher = true; w.onAllDesktops = true;",
            m = STRIP_MARGIN,
            top_js = PANEL_TOP_JS,
            width = STRIP_WIDTH,
            height = STRIP_HEIGHT,
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
    let primary_js = if school { primary_script(primary) } else { String::new() };
    format!(
        "{primary_js}for (const w of workspace.windowList()) {{\n\
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

/// School and orb rules share General/rules. Serialize the complete
/// read-modify-write/reload transaction. When both locks are needed, the
/// lifecycle gate comes first; these transactions never acquire it.
static RULES_MUTATION: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn mutate_rule_config<T>(gate: &std::sync::Mutex<()>, mutation: impl FnOnce() -> T) -> T {
    let _lock = gate.lock().unwrap_or_else(|e| e.into_inner());
    mutation()
}

/// Install a KWin window rule (idempotent) and reload rules. Fail-soft.
/// Shared with the Brave orb (orb.rs).
pub(crate) fn install_rule(rule_id: &str, entries: &[(&str, &str)]) {
    mutate_rule_config(&RULES_MUTATION, || install_rule_locked(rule_id, entries));
}

fn install_rule_locked(rule_id: &str, entries: &[(&str, &str)]) {
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
        .args(["--session", "--print-reply", "--dest=org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"])
        .output();
}

/// KWin script name of the dock keeper (stays loaded while docked).
const KEEPER_NAME: &str = "jarvis_school_keeper";
static KEEPER_GEN: AtomicU64 = AtomicU64::new(0);
static KEEPER_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

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
/// live primary output, with `primary` only an older-KWin fallback. Pure.
pub fn keeper_script(primary: Option<&str>, menu_extra: u32) -> String {
    format!(
        "{primary_js}const JK_TITLE = \"{title}\";\n\
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
         let jkBusy = false;\n\
         function jkDock() {{\n\
           if (jkBusy) return;\n\
           const w = jkStrip();\n\
           if (!w || w.minimized) return;\n\
           const o = jkPrimary();\n\
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
               const width = Math.min({width}, Math.max(1, p.a.width - 2 * {m}));\n\
               const h = Math.min({height} + {menu_extra}, Math.max(1, p.a.height - 2 * {m}));\n\
               want = {{x: p.a.x + p.a.width - width - {m}, y: p.a.y + p.a.height - h - {m}, width: width, height: h}};\n\
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
         try {{ workspace.screenOrderChanged.connect(jkSoon); }} catch (e) {{}}\n\
         try {{ workspace.stackingOrderChanged.connect(jkRaise); }} catch (e) {{}}\n\
         try {{ workspace.virtualScreenGeometryChanged.connect(jkSoon); }} catch (e) {{}}\n\
         try {{ workspace.windowAdded.connect(function(w) {{ jkHook(w); jkSoon(); }}); }} catch (e) {{}}\n\
         try {{ workspace.windowRemoved.connect(function(w) {{ jkSoon(); }}); }} catch (e) {{}}\n\
         try {{ workspace.currentDesktopChanged.connect(jkSoon); }} catch (e) {{}}\n\
         for (const w of workspace.windowList()) jkHook(w);\n\
         jkDock();\n",
        primary_js = primary_script(primary),
        title = STRIP_TITLE,
        m = STRIP_MARGIN,
        width = STRIP_WIDTH,
        height = STRIP_HEIGHT,
        menu_extra = menu_extra.min(MENU_MAX_EXTRA),
        inset_js = PANEL_INSET_JS,
    )
}

/// Load the dock keeper (replacing any running one). Fail-soft.
fn start_keeper() {
    let gen = TX_GEN.load(Ordering::SeqCst);
    let keeper_gen = KEEPER_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    std::thread::spawn(move || {
        let primary = primary_output_name();
        let extra = MENU_EXTRA_NOW.load(Ordering::SeqCst);
        let script = with_panel_thickness(&keeper_script(primary.as_deref(), extra));
        // Serialize replacement/unload: an older menu request must never
        // overwrite or unload a newer keeper after a slow D-Bus lookup.
        // Lock order is always lifecycle, then keeper.
        let _lifecycle = LIFECYCLE.lock();
        let _lock = KEEPER_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Re-checked on this thread: an exit or entry that began since the
        // keeper was asked for has already stopped it, and loading it now
        // would snap the window back to bar height mid-transition.
        if !is_school() || in_transition() || TX_GEN.load(Ordering::SeqCst) != gen
            || KEEPER_GEN.load(Ordering::SeqCst) != keeper_gen {
            return;
        }
        load_kwin_named(&script, KEEPER_NAME);
    });
}

/// Unload the dock keeper, so it never fights a transition or the HUD.
fn stop_keeper() {
    KEEPER_GEN.fetch_add(1, Ordering::SeqCst);
    let _lock = KEEPER_LOCK.lock().unwrap_or_else(|e| e.into_inner());
    unload_kwin_named(KEEPER_NAME);
}

/// Check ownership and perform native effects atomically; the one-shot's
/// 300 ms retention must never block the next main-thread school stage.
fn run_guarded_kwin(
    script: &str,
    name: &str,
    current: impl FnOnce() -> bool,
    before_load: impl FnOnce(),
) -> bool {
    let started = LIFECYCLE.start_one_shot(name, current, |name| {
        before_load();
        load_kwin_named(script, name)
    });
    if let Some(script) = started {
        script.finish();
        true
    } else {
        false
    }
}

/// Run a school-mode KWin script (one that pins the window above everything)
/// only while school mode is still on. Every such script is started from a
/// thread that slept first (compositor settle, transition steps); if Sir
/// switched back to normal in the meantime a late one would re-pin the HUD
/// above all windows after the restore had cleared it. That was the "stuck
/// always on top after school mode" bug.
/// A slow monitor/Plasma lookup from an earlier animation must not move
/// the overlay after a reversal starts a new transition generation.
fn run_school_stage_kwin(script: &str, name: &str, gen: u64) {
    run_guarded_kwin(script, name, || current_mode(true, gen), || {});
}

/// KWin script that makes the normal HUD a normal window again: not kept
/// above, back in the taskbar/switcher/pager, on one desktop. Touches only
/// the HUD ("Jarvis" exactly, so never the school strip) and never moves or
/// resizes it. Idempotent, so it is safe to run repeatedly. Pure.
pub fn normalize_script() -> String {
    format!(
        "for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"{hud}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           w.keepAbove = false; w.skipTaskbar = false; w.skipPager = false; w.skipSwitcher = false; w.onAllDesktops = false;\n\
         }}\n",
        hud = HUD_TITLE
    )
}

/// Rules list without `id`. Pure.
pub fn rules_without(existing: &str, id: &str) -> String {
    existing
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty() && *s != id)
        .collect::<Vec<_>>()
        .join(",")
}

/// Remove a KWin window rule from the active list and reload rules, so a
/// forced "keep above" can never outlive the mode that wanted it. Fail-soft.
pub(crate) fn uninstall_rule(rule_id: &str) {
    mutate_rule_config(&RULES_MUTATION, || uninstall_rule_locked(rule_id));
}

fn uninstall_rule_locked(rule_id: &str) {
    let read = std::process::Command::new("kreadconfig6")
        .args(["--file", "kwinrulesrc", "--group", "General", "--key", "rules"])
        .output();
    let existing = match read {
        Ok(o) => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        Err(_) => return, // not Plasma
    };
    let rules = rules_without(&existing, rule_id);
    if rules == existing {
        return; // already gone
    }
    let write = |group: &str, key: &str, value: &str| {
        let _ = std::process::Command::new("kwriteconfig6")
            .args(["--file", "kwinrulesrc", "--group", group, "--key", key, value])
            .output();
    };
    let count = if rules.is_empty() { 0 } else { rules.split(',').count() };
    write("General", "count", &count.to_string());
    write("General", "rules", &rules);
    let _ = std::process::Command::new("dbus-send")
        .args(["--session", "--print-reply", "--dest=org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure"])
        .output();
}

/// Put the HUD back to a normal, not-always-on-top window: drop the school
/// rule, clear every pin KWin holds, and clear Tauri's own flag. Only ever
/// acts in normal mode. Fail-soft; blocks for the script's lifetime, so
/// call it from a thread.
pub fn heal_normal_mode(app: Option<&AppHandle>) {
    heal_normal_generation(app, TX_GEN.load(Ordering::SeqCst));
}

fn heal_normal_generation(app: Option<&AppHandle>, gen: u64) {
    if !run_guarded_kwin(
        &normalize_script(), "jarvis_hud_normalize",
        || current_mode(false, gen), || uninstall_rule(RULE_ID),
    ) {
        return;
    }
    if let Some(app) = app {
        on_main_sync(app, move |app| {
            if current_mode(false, gen) {
                if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
                    let _ = win.set_always_on_top(false);
                }
            }
        });
    }
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

const BOOT_FILL_NAME: &str = "jarvis_boot_fill";

/// At launch (normal mode) the HUD fills the screen's work area but stays a
/// normal decorated window: drag the title bar to move it. Fail-soft.
pub fn fill_work_area_at_boot() {
    if is_school() {
        return;
    }
    let gen = TX_GEN.load(Ordering::SeqCst);
    std::thread::spawn(move || {
        // A previous run may have died in school mode with the HUD pinned
        // above everything: normal mode never starts that way.
        heal_normal_generation(None, gen);
        let mut loaded = false;
        run_in_mode(false, gen, || {
            loaded = load_kwin_named(&boot_fill_script(), BOOT_FILL_NAME);
        });
        if loaded {
            std::thread::sleep(std::time::Duration::from_secs(20));
        }
        run_in_mode(false, gen, || unload_kwin_named(BOOT_FILL_NAME));
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
    on_main_sync(app, enter_locked);
}

fn enter_locked(app: &AppHandle) {
    unload_kwin_named(BOOT_FILL_NAME);
    let was = SCHOOL.swap(true, Ordering::SeqCst);
    // Entered again mid-return: drop the return (its "restore" stage is
    // ignored from here on) and play the arrival from wherever we are.
    let returning = RETURNING.swap(false, Ordering::SeqCst);
    if was && !returning {
        apply_locked(app);
        return;
    }
    reset_suit_focus_locked(app);
    stop_keeper();
    MENU_EXTRA_NOW.store(0, Ordering::SeqCst);
    ENTERING.store(true, Ordering::SeqCst);
    let gen = TX_GEN.fetch_add(1, Ordering::SeqCst) + 1;
    let phase = if returning || !overlay_visible(app) { "arrive" } else { "collapse" };
    signal(app, phase);
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(TX_WATCHDOG_MS));
        on_main_sync(&app, move |app| {
            if current_mode(true, gen) && DOCKED_GEN.load(Ordering::SeqCst) != gen {
                eprintln!("jarvis: school transition stalled; docking directly");
                apply_locked(app);
            }
        });
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
    on_main_sync(app, |app| {
        reset_suit_focus_locked(app);
        unload_kwin_named(BOOT_FILL_NAME);
        TX_GEN.fetch_add(1, Ordering::SeqCst);
        SCHOOL.store(true, Ordering::SeqCst);
        RETURNING.store(false, Ordering::SeqCst);
        apply_locked(app);
    });
}

/// Transition generation: bumped on entry, return, and immediate restore,
/// so delayed effects from an earlier lifecycle never act on a newer one.
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
        "{primary_js}for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"Jarvis\" && w.caption !== \"{title}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           const out = w.output;\n\
           const prim = jkPrimary();\n\
           if (!out || !prim) continue;\n\
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
        primary_js = primary_script(primary),
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
                Cover::Primary => "jkPrimary()".to_string(),
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
                 if (!target) continue;\n\
                 if (w.output !== target) workspace.sendClientToScreen(w, target);\n\
                 w.noBorder = true;\n\
                 const g = target.geometry;\n\
                 w.frameGeometry = {{x: g.x, y: g.y, width: g.width, height: g.height}};"
            )
        }
    };
    let primary_js = if cover == Cover::Primary { primary_script(primary) } else { String::new() };
    format!(
        "{primary_js}for (const w of workspace.windowList()) {{\n\
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
    on_main_sync(&app, move |app| {
        school_stage_locked(app, stage, nonce, rect)
    }).unwrap_or_else(|| Err("school stage main-thread dispatch failed or timed out".into()))
}

fn school_stage_locked(
    app: &AppHandle,
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
                run_school_stage_kwin(
                    &with_panel_thickness(&measure_script(&nonce, primary.as_deref())),
                    "jarvis_school_measure",
                    gen,
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
            reset_suit_focus_locked(app);
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
                run_school_stage_kwin(&cover_script(cover, primary.as_deref(), content), "jarvis_school_cover", gen);
            });
        }
        "dock" => {
            reset_suit_focus_locked(app);
            DOCKED_GEN.store(gen, Ordering::SeqCst);
            ENTERING.store(false, Ordering::SeqCst);
            ensure_rule();
            strip_window_props(&app, false);
            std::thread::spawn(move || {
                let primary = primary_output_name();
                run_guarded_kwin(
                    &with_panel_thickness(&kwin_script(true, None, primary.as_deref())),
                    "jarvis_school_dock",
                    // A menu may open while the monitor lookup is pending.
                    || current_mode(true, gen) && MENU_EXTRA_NOW.load(Ordering::SeqCst) == 0,
                    || {},
                );
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
    menu_script_on_primary(extra, None)
}

pub fn menu_script_on_primary(extra: u32, primary: Option<&str>) -> String {
    let extra = extra.min(MENU_MAX_EXTRA);
    format!(
        "{primary_js}for (const w of workspace.windowList()) {{\n\
           if (w.caption !== \"{title}\") continue;\n\
           if (!String(w.resourceClass || \"\").toLowerCase().includes(\"jarvis\")) continue;\n\
           const target = jkPrimary();\n\
           if (!target) continue;\n\
           if (w.output !== target) workspace.sendClientToScreen(w, target);\n\
           {top_js}\
           if (panel >= 24) {{\n\
             w.frameGeometry = {{x: full.x + inset, y: top + inset - {extra}, width: full.width - 2 * inset, height: barH + {extra}}};\n\
           }} else {{\n\
             const width = Math.min({width}, Math.max(1, a.width - 2 * {m}));\n\
             const height = Math.min({height} + {extra}, Math.max(1, a.height - 2 * {m}));\n\
             w.frameGeometry = {{x: a.x + a.width - width - {m}, y: a.y + a.height - height - {m}, width: width, height: height}};\n\
           }}\n\
         }}\n",
        title = STRIP_TITLE,
        primary_js = primary_script(primary),
        top_js = PANEL_TOP_JS,
        width = STRIP_WIDTH,
        height = STRIP_HEIGHT,
        m = STRIP_MARGIN,
    )
}

/// Taskbar menus: grow the window upward by `extra` px so a menu can open
/// above the bar (the rest of that area is transparent), or shrink back
/// with 0. Only while docked in school mode.
#[tauri::command]
pub fn school_menu(extra: f64) -> Result<(), String> {
    let _lifecycle = LIFECYCLE.lock();
    // Mid-transition the window belongs to the animation: a menu closing as
    // the bar leaves must not resize it or re-arm the keeper.
    if !is_school() || crate::orb::is_orb() || in_transition() {
        return Ok(());
    }
    let extra = if extra.is_finite() { extra.max(0.0) as u32 } else { 0 };
    let extra = extra.min(MENU_MAX_EXTRA);
    let gen = TX_GEN.load(Ordering::SeqCst);
    MENU_EXTRA_NOW.store(extra, Ordering::SeqCst);
    std::thread::spawn(move || {
        let primary = primary_output_name();
        let script = with_panel_thickness(&menu_script_on_primary(extra, primary.as_deref()));
        run_guarded_kwin(
            &script, "jarvis_school_menu",
            || current_mode(true, gen) && !in_transition() && MENU_EXTRA_NOW.load(Ordering::SeqCst) == extra,
            || {},
        );
    });
    // Re-arm the keeper at the new height, or it would undo the menu.
    start_keeper();
    Ok(())
}

/// Window properties + dock for the strip. Also used by show_overlay.
pub fn apply(app: &AppHandle) {
    on_main_sync(app, |app| {
        if is_school() {
            apply_locked(app);
        }
    });
}

fn apply_locked(app: &AppHandle) {
    // A show/voice summon of an already docked modal or menu must not
    // collapse its window or revoke keyboard access without a UI close.
    // Decide before clearing ENTERING, so watchdog docking still resets.
    let plan = SUIT_FOCUS.lock().unwrap_or_else(|e| e.into_inner()).prepare_apply(
        TX_GEN.load(Ordering::SeqCst), in_transition(), MENU_EXTRA_NOW.load(Ordering::SeqCst),
    );
    MENU_EXTRA_NOW.store(plan.menu_extra, Ordering::SeqCst);
    if plan.preserve_geometry {
        if let Some(win) = app.get_webview_window(crate::commands::OVERLAY_LABEL) {
            let _ = win.show();
            let _ = win.set_focusable(plan.focusable);
            if plan.focusable {
                let _ = win.set_focus();
            }
            crate::commands::click_through_if_visible(&win, false);
        }
        start_keeper();
        return;
    }
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
    let gen = TX_GEN.load(Ordering::SeqCst);
    std::thread::spawn(move || {
        // Let the compositor map the resized window before docking it.
        std::thread::sleep(std::time::Duration::from_millis(400));
        let primary = primary_output_name();
        run_guarded_kwin(
            &with_panel_thickness(&kwin_script(true, None, primary.as_deref())),
            "jarvis_school_dock",
            // A newer menu/diagnostics expansion owns its geometry now.
            || current_mode(true, gen) && MENU_EXTRA_NOW.load(Ordering::SeqCst) == 0,
            || {},
        );
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
    on_main_sync(app, exit_locked);
}

fn exit_locked(app: &AppHandle) {
    reset_suit_focus_locked(app);
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
        on_main_sync(&app, move |app| {
            if current_mode(true, gen) && RETURNING.load(Ordering::SeqCst) {
                eprintln!("jarvis: school return stalled; restoring directly");
                restore(app);
            }
        });
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
    reset_suit_focus_locked(app);
    unload_kwin_named(BOOT_FILL_NAME);
    // Even two immediate returns need distinct generations: only the most
    // recent saved frame may be placed after its compositor settle delay.
    let gen = TX_GEN.fetch_add(1, Ordering::SeqCst) + 1;
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
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_millis(settle_ms));
        // Rule removal and placement belong to this return; retention and
        // cleanup of its unique script can safely overlap the next entry.
        if !run_guarded_kwin(
            &kwin_script(false, restore, None), "jarvis_school_restore",
            || current_mode(false, gen), || uninstall_rule(RULE_ID),
        ) {
            return;
        }
        // Late school scripts (compositor settle, a transition step) or
        // KWin re-applying a rule can re-pin the HUD after the restore
        // above. Re-clear after each of them could have landed, so
        // returning to normal always ends not-always-on-top.
        for delay_ms in NORMALIZE_PASSES_MS {
            std::thread::sleep(std::time::Duration::from_millis(delay_ms));
            if !current_mode(false, gen) {
                return;
            }
            heal_normal_generation(Some(&app), gen);
        }
    });
}

/// Delays (ms) of the follow-up clean-ups after leaving school mode.
const NORMALIZE_PASSES_MS: [u64; 3] = [600, 1500, 3500];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suit_focus_changes_only_the_forced_focus_rule() {
        assert_eq!(suit_focus_rule_entries(true), [("acceptfocus", "true"), ("acceptfocusrule", "2")]);
        assert_eq!(suit_focus_rule_entries(false), [("acceptfocus", "false"), ("acceptfocusrule", "2")]);
        assert!(rule_entries().contains(&("acceptfocus", "false")));
    }

    #[test]
    fn suit_focus_requires_a_visible_docked_school_window() {
        let mut focus = SuitFocusState::default();
        // Normal HUD, hidden strip, orb, and transitions are ineligible.
        assert_eq!(focus.request(true, None, 1, 0, false), SuitFocusChange::Unchanged);
        assert!(focus.active.is_none());
        assert_eq!(focus.request(true, None, 1, 0, true), SuitFocusChange::Enable(1));
        assert_eq!(focus.request(false, Some(1), 1, 0, true), SuitFocusChange::Disable);
        assert!(focus.active.is_none());
    }

    #[test]
    fn old_suit_close_cannot_disable_a_newer_modal() {
        let mut focus = SuitFocusState::default();
        assert_eq!(focus.request(true, None, 1, 0, true), SuitFocusChange::Enable(1));
        assert_eq!(focus.request(true, None, 1, 0, true), SuitFocusChange::Enable(2));
        assert_eq!(focus.request(false, Some(1), 1, 0, true), SuitFocusChange::Unchanged);
        assert_eq!(focus.active, Some((1, 2)));
        assert_eq!(focus.request(false, Some(2), 1, 0, true), SuitFocusChange::Disable);
    }

    #[test]
    fn suit_reload_reset_revokes_orphans_and_queued_acquires() {
        let mut focus = SuitFocusState::default();
        focus.request(true, None, 1, 0, true);
        assert_eq!(focus.request(false, None, 1, 0, true), SuitFocusChange::Disable);
        assert!(focus.active.is_none());
        assert_eq!(focus.request(true, None, 1, 0, true), SuitFocusChange::Unchanged);
        assert_eq!(focus.request(true, None, 1, 1, true), SuitFocusChange::Enable(2));
        // An older page's token cannot revoke the post-reset lease.
        assert_eq!(focus.request(false, Some(1), 1, 0, true), SuitFocusChange::Unchanged);
        assert_eq!(focus.active, Some((1, 2)));
    }

    #[test]
    fn suit_transition_reset_invalidates_focus_even_without_a_prior_lease() {
        let mut focus = SuitFocusState::default();
        assert_eq!(focus.reset(), SuitFocusChange::Unchanged);
        assert_eq!(focus.request(true, None, 1, 0, true), SuitFocusChange::Unchanged);
        assert_eq!(focus.request(true, None, 2, 1, true), SuitFocusChange::Enable(1));
        assert_eq!(focus.reset(), SuitFocusChange::Disable);
        assert_eq!(focus.request(true, None, 2, 1, true), SuitFocusChange::Unchanged);
        assert!(focus.active.is_none());
    }

    #[test]
    fn docked_show_or_repeated_entry_preserves_suit_focus_and_expansion() {
        let mut focus = SuitFocusState::default();
        focus.request(true, None, 4, 0, true);
        for _ in 0..2 { // show/apply, then an already-on school entry
            assert_eq!(focus.prepare_apply(4, false, 740), SchoolApplyPlan {
                preserve_geometry: true, focusable: true, menu_extra: 740,
            });
            assert_eq!(focus.active, Some((4, 1)));
            assert_eq!(focus.epoch, 0);
        }
        // The modal still closes with the original lease.
        assert_eq!(focus.request(false, Some(1), 4, 0, true), SuitFocusChange::Disable);
    }

    #[test]
    fn transition_apply_revokes_suit_focus_and_expansion() {
        let mut focus = SuitFocusState::default();
        focus.request(true, None, 4, 0, true);
        assert_eq!(focus.prepare_apply(4, true, 740), SchoolApplyPlan {
            preserve_geometry: false, focusable: false, menu_extra: 0,
        });
        assert!(focus.active.is_none());
        assert_eq!(focus.request(true, None, 4, 0, true), SuitFocusChange::Unchanged);
        // Ordinary taskbar menus retain their height, without taking keys.
        assert_eq!(focus.prepare_apply(4, false, 360), SchoolApplyPlan {
            preserve_geometry: true, focusable: false, menu_extra: 360,
        });
    }

    #[test]
    fn ordinary_show_does_not_invalidate_a_pending_suit_acquire() {
        let mut focus = SuitFocusState::default();
        assert_eq!(focus.prepare_apply(4, false, 0), SchoolApplyPlan {
            preserve_geometry: false, focusable: false, menu_extra: 0,
        });
        assert_eq!(focus.request(true, None, 4, 0, true), SuitFocusChange::Enable(1));
    }

    fn assert_serialized_rule_mutations(
        initial: &str,
        first: fn(&str) -> String,
        second: fn(&str) -> String,
        expected: &str,
    ) {
        use std::sync::{mpsc, Arc, Mutex};
        let gate = Arc::new(Mutex::new(()));
        let rules = Arc::new(Mutex::new(initial.to_owned()));
        let (read_tx, read_rx) = mpsc::channel();
        let (finish_tx, finish_rx) = mpsc::channel();
        let first_gate = gate.clone();
        let first_rules = rules.clone();
        let first_worker = std::thread::spawn(move || {
            mutate_rule_config(&first_gate, || {
                let snapshot = first_rules.lock().unwrap().clone();
                read_tx.send(()).unwrap();
                finish_rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap();
                // The transaction must retain the gate between read/write.
                assert!(first_gate.try_lock().is_err());
                *first_rules.lock().unwrap() = first(&snapshot);
            });
        });
        read_rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap();
        let (attempt_tx, attempt_rx) = mpsc::channel();
        let second_gate = gate.clone();
        let second_rules = rules.clone();
        let second_worker = std::thread::spawn(move || {
            attempt_tx.send(()).unwrap();
            mutate_rule_config(&second_gate, || {
                let snapshot = second_rules.lock().unwrap().clone();
                *second_rules.lock().unwrap() = second(&snapshot);
            });
        });
        attempt_rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap();
        finish_tx.send(()).unwrap();
        first_worker.join().unwrap();
        second_worker.join().unwrap();
        assert_eq!(*rules.lock().unwrap(), expected);
    }

    #[test]
    fn simultaneous_rule_installs_preserve_both_and_unrelated_rules() {
        assert_serialized_rule_mutations(
            "other", |rules| rules_with(rules, "orb"), |rules| rules_with(rules, RULE_ID),
            "other,orb,jarvis-school-strip",
        );
    }

    #[test]
    fn orb_rule_removal_cannot_overwrite_a_school_rule_install() {
        assert_serialized_rule_mutations(
            "other,orb", |rules| rules_without(rules, "orb"), |rules| rules_with(rules, RULE_ID),
            "other,jarvis-school-strip",
        );
    }

    /// In-memory native effects, using the same gate and generation guard
    /// as delayed restores. These tests never create a window or call KWin.
    #[derive(Default)]
    struct LifecycleFixture {
        gate: LifecycleGate,
        school: AtomicBool,
        generation: AtomicU64,
        rule_installed: AtomicBool,
        geometry: std::sync::Mutex<&'static str>,
    }

    impl LifecycleFixture {
        fn transition(&self, school: bool) -> u64 {
            let _lock = self.gate.lock();
            self.school.store(school, Ordering::SeqCst);
            let gen = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
            if school {
                self.rule_installed.store(true, Ordering::SeqCst);
                *self.geometry.lock().unwrap() = "strip";
            }
            gen
        }

        fn restore(&self, gen: u64, geometry: &'static str) -> bool {
            self.gate.run_for_mode(&self.school, &self.generation, false, gen, || {
                self.rule_installed.store(false, Ordering::SeqCst);
                *self.geometry.lock().unwrap() = geometry;
            })
        }
    }

    #[test]
    fn delayed_return_cannot_remove_reentry_rule_or_move_strip() {
        let native = LifecycleFixture::default();
        native.transition(true);
        let old_return = native.transition(false);
        native.transition(true); // re-enter during either settle delay
        assert!(!native.restore(old_return, "old HUD"));
        assert!(native.rule_installed.load(Ordering::SeqCst));
        assert_eq!(*native.geometry.lock().unwrap(), "strip");
    }

    #[test]
    fn delayed_return_cannot_overwrite_a_newer_return() {
        let native = LifecycleFixture::default();
        native.transition(true);
        let old_return = native.transition(false);
        native.transition(true);
        let new_return = native.transition(false);
        assert!(native.restore(new_return, "new HUD"));
        // Mode is normal again, so a mode-only guard would place old HUD.
        assert!(!native.restore(old_return, "old HUD"));
        assert_eq!(*native.geometry.lock().unwrap(), "new HUD");
        assert!(!native.rule_installed.load(Ordering::SeqCst));
    }

    #[test]
    fn delayed_school_stage_cannot_move_a_new_entry() {
        let native = LifecycleFixture::default();
        let old_entry = native.transition(true);
        native.transition(false);
        native.transition(true);
        assert!(!native.gate.run_for_mode(
            &native.school, &native.generation, true, old_entry,
            || *native.geometry.lock().unwrap() = "old fullscreen cover",
        ));
        assert_eq!(*native.geometry.lock().unwrap(), "strip");
    }

    #[test]
    fn lifecycle_gate_holds_until_delayed_effect_finishes() {
        use std::sync::{mpsc, Arc};
        let native = Arc::new(LifecycleFixture::default());
        let old_return = native.transition(false);
        let (checked_tx, checked_rx) = mpsc::channel();
        let (finish_tx, finish_rx) = mpsc::channel();
        let worker_native = native.clone();
        let worker = std::thread::spawn(move || {
            worker_native.gate.run_for_mode(
                &worker_native.school, &worker_native.generation, false, old_return,
                || {
                    checked_tx.send(()).unwrap();
                    finish_rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap();
                    worker_native.rule_installed.store(false, Ordering::SeqCst);
                    *worker_native.geometry.lock().unwrap() = "old HUD";
                },
            )
        });
        checked_rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap();
        // Deterministically pause between the eligibility check and effects.
        // An entry cannot acquire its gate anywhere inside that interval.
        let held_during_effect = native.gate.0.try_lock().is_err();
        finish_tx.send(()).unwrap();
        assert!(worker.join().unwrap());
        assert!(held_during_effect);
        native.transition(true);
        assert!(native.rule_installed.load(Ordering::SeqCst));
        assert_eq!(*native.geometry.lock().unwrap(), "strip");
    }

    #[test]
    fn one_shot_retention_allows_next_transition_and_preserves_its_script() {
        use std::collections::HashSet;
        use std::sync::{mpsc, Arc, Mutex};
        let native = Arc::new(LifecycleFixture::default());
        native.transition(true);
        let scripts = Arc::new(Mutex::new(HashSet::new()));
        let old = native.gate.start_one_shot("measure", || true, |name| {
            assert!(native.gate.0.try_lock().is_err());
            scripts.lock().unwrap().insert(name.to_owned());
            true
        }).unwrap();
        let old_name = old.name.clone();
        let (retaining_tx, retaining_rx) = mpsc::channel();
        let (finish_tx, finish_rx) = mpsc::channel();
        let worker_native = native.clone();
        let worker_scripts = scripts.clone();
        let worker = std::thread::spawn(move || {
            old.finish_with(
                || {
                    assert!(worker_native.gate.0.try_lock().is_ok());
                    retaining_tx.send(()).unwrap();
                    finish_rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap();
                },
                |name| {
                    assert!(worker_native.gate.0.try_lock().is_ok());
                    assert!(worker_scripts.lock().unwrap().remove(name));
                },
            );
        });
        retaining_rx.recv_timeout(std::time::Duration::from_secs(5)).unwrap();
        // Pause the old script in retention, then perform the next mode
        // change and native stage without allowing that retention to end.
        assert!(native.gate.0.try_lock().is_ok());
        native.transition(false);
        let new = native.gate.start_one_shot("measure", || true, |name| {
            scripts.lock().unwrap().insert(name.to_owned());
            true
        }).unwrap();
        assert_ne!(old_name, new.name);
        finish_tx.send(()).unwrap();
        worker.join().unwrap();
        // Cleanup targets its invocation, not the shared purpose name.
        assert!(!scripts.lock().unwrap().contains(&old_name));
        assert!(scripts.lock().unwrap().contains(&new.name));
        new.finish_with(|| {}, |name| { scripts.lock().unwrap().remove(name); });
        assert!(scripts.lock().unwrap().is_empty());
    }

    #[test]
    fn stale_one_shot_never_starts_native_effects() {
        let native = LifecycleFixture::default();
        let old_gen = native.transition(false);
        native.transition(true);
        native.transition(false);
        let stale = native.gate.start_one_shot(
            "restore",
            || !native.school.load(Ordering::SeqCst)
                && native.generation.load(Ordering::SeqCst) == old_gen,
            |_| panic!("stale restore must not remove a rule or load a script"),
        );
        assert!(stale.is_none());
    }

    #[test]
    fn failed_one_shot_load_cleans_its_identity_without_retention() {
        let gate = LifecycleGate::new();
        let script = gate.start_one_shot("measure", || true, |_| false).unwrap();
        let name = script.name.clone();
        let mut cleaned = false;
        script.finish_with(
            || panic!("failed loads need no retention delay"),
            |cleanup_name| {
                assert!(gate.0.try_lock().is_ok());
                assert_eq!(cleanup_name, name);
                cleaned = true;
            },
        );
        assert!(cleaned);
    }

    #[test]
    fn normalize_unpins_only_the_hud() {
        let s = normalize_script();
        assert!(s.contains("w.keepAbove = false"));
        assert!(s.contains("w.caption !== \"Jarvis\""));
        assert!(s.contains("resourceClass"));
        // Never the school strip, and never moves or resizes anything.
        assert!(!s.contains(STRIP_TITLE));
        assert!(!s.contains("frameGeometry"));
        assert!(!s.contains("keepAbove = true"));
    }

    #[test]
    fn restore_script_always_unpins() {
        for restore in [None, Some((1, 2, 1280, 720))] {
            let s = kwin_script(false, restore, None);
            assert!(s.contains("keepAbove = false") && !s.contains("keepAbove = true"));
        }
    }

    #[test]
    fn rules_without_removes_only_ours() {
        assert_eq!(rules_without("a,jarvis-school-strip,b", RULE_ID), "a,b");
        assert_eq!(rules_without(RULE_ID, RULE_ID), "");
        assert_eq!(rules_without("a,b", RULE_ID), "a,b");
        assert_eq!(rules_without("", RULE_ID), "");
        // Round trip with the installer.
        assert_eq!(rules_without(&rules_with_ours("x"), RULE_ID), "x");
    }

    #[test]
    fn school_scripts_never_run_in_normal_mode() {
        // Would shell out to dbus-send if it ran; in normal mode it must
        // return before touching KWin at all.
        run_school_stage_kwin("w.keepAbove = true;", "jarvis_test_never_loaded", TX_GEN.load(Ordering::SeqCst));
        assert!(!is_school());
    }

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
        // Live primary selection wins over a cached connector or focus.
        assert!(dock.contains("JK_PRIMARY = \"DP-1\"") && dock.contains("workspace.screenOrder"));
        assert!(!dock.contains("activeScreen"));
        assert!(dock.contains("sendClientToScreen(w, target)"));
        // Output assignment can settle later: never measure via w.output.
        assert!(dock.contains("KWin.FullScreenArea, target, workspace.currentDesktop"));
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
        assert!(s.contains("d.dock") && s.contains("d.output !== target"));
        assert!(menu_script(0).contains("height: barH + 0"));
        assert!(menu_script(99_999).contains(&format!("barH + {MENU_MAX_EXTRA}")));
    }

    #[test]
    fn measure_script_reports_to_bridge_with_clean_nonce() {
        let s = measure_script("ab12\"; evil()", Some("eDP-1"));
        assert!(s.contains("callDBus(\"org.jarvis.Focus\"") && s.contains("\"SchoolGeom\""));
        assert!(s.contains("nonce: \"ab12evil\"") && !s.contains("evil()"));
        assert!(s.contains("JK_PRIMARY = \"eDP-1\"") && s.contains("w.clientGeometry"));
        assert!(s.contains("panel: Math.round"));
    }

    #[test]
    fn cover_script_fills_current_or_primary_screen() {
        let here = cover_script(Cover::Here, Some("eDP-1"), None);
        assert!(here.contains("const target = w.output;") && !here.contains("eDP-1"));
        let prim = cover_script(Cover::Primary, Some("eDP-1"), None);
        assert!(prim.contains("JK_PRIMARY = \"eDP-1\"") && prim.contains("sendClientToScreen"));
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
        // Re-evaluate primary changes while both monitors remain attached.
        assert!(k.contains("workspace.screenOrderChanged.connect(jkSoon)"));
        assert!(k.contains("const o = jkPrimary();") && !k.contains("activeScreen"));
        // A primary without a panel still receives a slim 620x48 strip.
        assert!(k.contains("Math.min(620,") && k.contains("Math.min(48 + 0,"));
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
