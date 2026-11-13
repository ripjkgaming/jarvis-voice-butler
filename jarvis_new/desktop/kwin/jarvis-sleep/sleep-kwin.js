// jarvis-sleep compositor side. Loaded for one run at a time by sleep_ctl.py through
// kwin_bridge_loader. KWin JS cannot write files, so every result leaves over D-Bus
// (org.jarvis.SleepCtl) and the Python receiver owns ~/.jarvis/sleep_windows.json.
// Sleep never focuses or activates a window. Wake activates only the window that was
// active before sleep, and only after everything else has been placed.
'use strict';
var SERVICE = 'org.jarvis.SleepCtl', OBJECT = '/Sleep', IFACE = 'org.jarvis.SleepCtl';
var SAVER_TAG = 'JARVIS-SLEEP';
var STAGGER_MIN = 40, STAGGER_MAX = 80;   // ms between window starts
var TOTAL_MS = 1200, MIN_MS = 350, TICK_MS = 16;
var MARGIN = 16;
var wakeTimer = null;   // global so the QTimer is not garbage collected mid-run
var runOp = '', runPids = null;

function text(v) { return v === undefined || v === null ? '' : String(v); }
function rect(g) { return {x: Math.round(g.x), y: Math.round(g.y), width: Math.round(g.width), height: Math.round(g.height)}; }
function call(method, arg, cb) {
    if (cb) callDBus(SERVICE, OBJECT, IFACE, method, arg, cb);
    else callDBus(SERVICE, OBJECT, IFACE, method, arg);
}
function finish(status, extra) {
    var out = extra || {};
    out.op = runOp;
    out.status = status;
    call('Done', JSON.stringify(out));
}

function isShell(w) { return !!(w.desktopWindow || w.dock || w.specialWindow); }
function isSaver(w) { return text(w.caption).indexOf(SAVER_TAG) >= 0; }
// Jarvis's own windows (the HUD shell, hologram/overlay windows) are never hidden or recorded.
function isJarvis(w) {
    var cls = (text(w.resourceClass) + ' ' + text(w.desktopFileName)).toLowerCase();
    var cap = text(w.caption).toLowerCase();
    return cls.indexOf('jarvis') >= 0 || cap === 'jarvis' || cap.indexOf('jarvis ') === 0 ||
        cap.indexOf('hologram') >= 0 || /(^|[^a-z])hud([^a-z]|$)/.test(cap);
}
// Normal user windows only. The desktop, panels, notifications, the screensaver and
// every Jarvis window are never touched.
function candidate(w) {
    if (!w || w.deleted || isSaver(w) || isShell(w) || isJarvis(w)) return false;
    if (runPids && runPids.indexOf(w.pid) < 0) return false;
    return !!w.normalWindow;
}
function record(w) {
    var desks = [];
    w.desktops.forEach(function (d) { desks.push(text(d.id)); });
    return {
        id: text(w.internalId), caption: text(w.caption), resourceClass: text(w.resourceClass),
        desktopFileName: text(w.desktopFileName), pid: w.pid,
        geometry: rect(w.frameGeometry), desktops: desks, onAllDesktops: !!w.onAllDesktops,
        maximized: !!(w.maximizedHorizontally && w.maximizedVertically),
        fullScreen: !!w.fullScreen, minimized: !!w.minimized, overlay: false,
        active: !!w.active
    };
}
function candidates() {
    var out = [];
    workspace.windowList().forEach(function (w) { if (candidate(w)) out.push(w); });
    return out;
}

function doSleep() {
    var active = workspace.activeWindow;
    var targets = candidates();
    var state = {
        version: 1, saved: Date.now(),
        active: active ? text(active.internalId) : '',
        windows: targets.map(record)
    };
    // Minimise only after the Python receiver has durably recorded the state.
    call('Record', JSON.stringify(state), function (reply) {
        if (reply !== 'ok') { finish('refused', {error: reply}); return; }
        var count = 0;
        targets.forEach(function (w) {
            if (w.deleted) return;
            if (!w.minimized) w.minimized = true;
            count++;
        });
        finish('ok', {count: count, windows: state.windows.length});
    });
}

function doList() {
    var list = candidates().map(record);
    finish('ok', {windows: list});
}

function screenFor(g) {
    var cx = g.x + g.width / 2, cy = g.y + g.height / 2, hit = null;
    workspace.screens.forEach(function (s) {
        var sg = s.geometry;
        if (!hit && cx >= sg.x && cx < sg.x + sg.width && cy >= sg.y && cy < sg.y + sg.height) hit = s;
    });
    return hit || workspace.activeScreen || workspace.screens[0];
}
function areaFor(g) {
    return workspace.clientArea(KWin.MaximizeArea, screenFor(g), workspace.currentDesktop);
}
function scatterRect(saved, area) {
    var width = Math.min(saved.width, area.width - 2 * MARGIN);
    var height = Math.min(saved.height, area.height - 2 * MARGIN);
    var x = area.x + MARGIN + Math.random() * Math.max(0, area.width - 2 * MARGIN - width);
    var y = area.y + MARGIN + Math.random() * Math.max(0, area.height - 2 * MARGIN - height);
    return {x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height)};
}
function easeOut(k) { return 1 - Math.pow(1 - k, 3); }

// Find the live window for a record: by KWin internal id first. The fallback matches
// only a minimised window with the same caption and class, so a window the user opened
// after sleep is never moved.
function findLive(rec, live, used) {
    for (var i = 0; i < live.length; i++) {
        var w = live[i];
        if (!w.deleted && !used[text(w.internalId)] && text(w.internalId) === rec.id) return w;
    }
    var hits = live.filter(function (w) {
        return !w.deleted && !used[text(w.internalId)] && w.minimized &&
            text(w.caption) === rec.caption && text(w.resourceClass) === rec.resourceClass;
    });
    return hits.length === 1 ? hits[0] : null;
}

function doWake(state) {
    if (!state || !state.windows) { finish('error', {error: 'no sleep state'}); return; }
    var live = workspace.windowList(), used = {}, plan = [], activeId = text(state.active);
    state.windows.forEach(function (rec) {
        var w = findLive(rec, live, used);
        if (!w) return;                         // closed while asleep: never recreated
        used[text(w.internalId)] = true;
        if (rec.minimized) return;              // hidden before sleep: stays hidden
        plan.push({w: w, rec: rec, isActive: text(rec.id) === activeId});
    });
    // Previously active window restores last.
    plan.sort(function (a, b) { return (a.isActive ? 1 : 0) - (b.isActive ? 1 : 0); });

    // Unminimise and scatter in the same tick, before KWin paints anything.
    plan.forEach(function (p) {
        var w = p.w, rec = p.rec;
        w.minimized = false;
        w.setMaximize(false, false);
        w.fullScreen = false;
        var ids = {};
        rec.desktops.forEach(function (id) { ids[id] = true; });
        var targets = workspace.desktops.filter(function (d) { return ids[text(d.id)]; });
        if (rec.onAllDesktops) w.onAllDesktops = true;
        else if (targets.length) w.desktops = targets;
        p.to = rect(rec.geometry);
        p.from = scatterRect(p.to, areaFor(p.to));
        w.frameGeometry = p.from;
    });

    // Stagger starts and give each window whatever time is left of TOTAL_MS.
    var t = 0;
    plan.forEach(function (p) {
        p.start = t;
        p.dur = Math.max(MIN_MS, TOTAL_MS - t);
        p.done = false;
        t += STAGGER_MIN + Math.random() * (STAGGER_MAX - STAGGER_MIN);
    });

    var t0 = Date.now();
    wakeTimer = new QTimer();
    wakeTimer.interval = TICK_MS;
    wakeTimer.timeout.connect(function () {
        var now = Date.now() - t0, pending = 0;
        plan.forEach(function (p) {
            if (p.done) return;
            if (p.w.deleted) { p.done = true; return; }
            var k = (now - p.start) / p.dur;
            if (k < 0) { pending++; return; }
            if (k >= 1) { p.w.frameGeometry = p.to; p.done = true; return; }
            var e = easeOut(k);
            p.w.frameGeometry = {
                x: Math.round(p.from.x + (p.to.x - p.from.x) * e),
                y: Math.round(p.from.y + (p.to.y - p.from.y) * e),
                width: Math.round(p.from.width + (p.to.width - p.from.width) * e),
                height: Math.round(p.from.height + (p.to.height - p.from.height) * e)
            };
            pending++;
        });
        if (pending === 0) {
            wakeTimer.stop();
            wakeFinish(plan, Date.now() - t0);
        }
    });
    wakeTimer.start();
}

function wakeFinish(plan, elapsed) {
    var restored = 0, active = null;
    plan.forEach(function (p) {
        var w = p.w;
        if (w.deleted) return;
        // Maximise and fullscreen are applied after the animation, to the saved rectangle.
        if (p.rec.fullScreen) w.fullScreen = true;
        else if (p.rec.maximized) w.setMaximize(true, true);
        restored++;
        if (p.isActive) active = w;
    });
    if (active && !active.deleted) workspace.activeWindow = active;
    finish('ok', {restored: restored, elapsed_ms: elapsed});
}

// Entry point: ask the receiver what this run should do.
call('Request', 'ready', function (reply) {
    var cmd = null;
    try { cmd = JSON.parse(reply); } catch (e) { finish('error', {error: 'bad command: ' + reply}); return; }
    runOp = text(cmd.op);
    runPids = cmd.pids && cmd.pids.length ? cmd.pids : null;
    try {
        if (cmd.op === 'sleep') doSleep();
        else if (cmd.op === 'wake') doWake(cmd.state);
        else if (cmd.op === 'list') doList();
        else finish('error', {error: cmd.error || 'unknown op'});
    } catch (e) {
        finish('error', {error: String(e)});
    }
});
