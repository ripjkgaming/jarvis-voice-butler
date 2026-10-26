/* SPDX-License-Identifier: MIT */
"use strict";

// Jarvis ASCII: every window, notification, popup, focus change, minimize,
// maximize/tile and desktop switch passes through a cyan glyph mosaic.
// Window dragging is deliberately left to jarvis-energy (DragOnly=true).
var ascii = {
    MODES: { window: 0, dock: 1, scan: 2, notify: 3, popup: 4, desktop: 5, geometry: 6 },
    shaders: {},
    strong: 0,
    requests: [],
    seen: [],
    locked: false,

    configure: function () {
        ascii.reduced = effect.readConfig("ReducedMotion", false);
        ascii.debug = effect.readConfig("Debug", false);
        ascii.focusScan = effect.readConfig("FocusScan", true);
        ascii.desktopSwitch = effect.readConfig("DesktopSwitch", true);
        // SlowMotion multiplies every duration (inspection aid; 1 = normal).
        var slow = Math.max(1, Number(effect.readConfig("SlowMotion", 1)) || 1);
        function at(ms) { return animationTime(ms) * slow; }
        ascii.t = {
            open: at(460), close: at(360), dock: at(520),
            scan: at(240), notifyIn: at(640), notifyOut: at(420),
            popupIn: at(170), popupOut: at(130), desktop: at(420),
            geometry: at(380)
        };
        ascii.receive();
    },

    // Jarvis voice actions arrive as a short-lived token (src/system/cinematic.py).
    receive: function () {
        try {
            var r = JSON.parse(effect.readConfig("ActionRequest", "{}"));
            if (!r.token || r.expires < Date.now() || r.expires > Date.now() + 10000 || ascii.seen.indexOf(r.token) >= 0) return;
            ascii.seen.push(r.token); ascii.seen = ascii.seen.slice(-32);
            ascii.requests = ascii.requests.filter(function (x) { return x.expires > Date.now(); });
            if (r.action === "open" && r.classes && r.classes.length) {
                ascii.requests.push(r); ascii.requests = ascii.requests.slice(-16); return;
            }
            var ids = r.windows && r.windows.length <= 9 ? r.windows : [{ pid: r.pid, caption: r.caption }];
            ids.forEach(function (identity) {
                var found = effects.stackingOrder.filter(function (w) { return w.pid === identity.pid && w.caption === identity.caption; });
                if (found.length === 1) {
                    found[0].asciiCommand = r;
                    if ((r.action === "focus" || r.action === "interact") && !found[0].minimized) ascii.scan(found[0], true);
                }
            });
        } catch (ignore) {}
    },
    commanded: function (w, kind) {
        var r = w.asciiCommand;
        if (r && r.expires > Date.now() && (r.action === kind || (r.action === "focus" && kind === "restore") ||
            (kind === "geometry" && /^(geometry|maximize|interact)$/.test(r.action)))) {
            delete w.asciiCommand; return true;
        }
        if (kind !== "open") return false;
        var names = String(w.windowClass || "").toLowerCase().split(/\s+/);
        for (var i = 0; i < ascii.requests.length; i++) {
            if (ascii.requests[i].classes.some(function (c) { return names.indexOf(c) >= 0; })) {
                ascii.requests.splice(i, 1); return true;
            }
        }
        return false;
    },

    blocked: function (w) {
        return ascii.locked || ascii.reduced || effects.hasActiveFullScreenEffect || !w ||
            w.lockScreen || w.outline || w.desktopWindow || w.dock || w.splash ||
            /kscreenlocker|ksmserver|ksplash|jarvis[-_ ]?watch|polkit|kdesu|pinentry|kwallet|spectacle/i.test(w.windowClass);
    },
    normal: function (w) {
        return !ascii.blocked(w) && w.managed && !w.fullScreen && !w.popupWindow && !w.notification &&
            !w.onScreenDisplay && (w.normalWindow || w.dialog) &&
            !(/plasmashell/i.test(w.windowClass) && !w.hasDecoration);
    },
    surface: function (w) {
        if (ascii.blocked(w)) return null;
        if (w.notification || w.criticalNotification) return "notify";
        if (w.onScreenDisplay) return "notify";
        if (w.popupWindow || w.appletPopup || w.tooltip || w.dropdownMenu || w.popupMenu || w.comboBox) return "popup";
        return null;
    },

    stop: function (w) {
        var s = w && w.ascii;
        if (!s) return;
        try { cancel(s.ids); } catch (ignore) {}
        delete w.ascii;
    },
    shader: function (mode, strong) {
        return ascii.shaders[mode + (strong ? "!" : "")] || 0;
    },
    play: function (w, kind, mode, o) {
        ascii.stop(w);
        var anims = (o.extra || []).slice();
        var shader = ascii.shader(mode, o.strong);
        if (shader) {
            anims.push({ type: Effect.ShaderUniform, fragmentShader: shader, uniform: "phase", from: o.from, to: o.to });
        } else if (mode !== ascii.MODES.scan && mode !== ascii.MODES.geometry) {
            anims.push({ type: Effect.Opacity, from: o.from, to: o.to });
        }
        if (!anims.length) return;
        if (ascii.debug) console.warn("Jarvis ASCII play " + kind + " " + w.windowClass + " " + o.duration + "ms shader=" + shader);
        var state = { kind: kind, role: o.role, ids: [], remaining: anims.length };
        w.ascii = state;
        try {
            var args = { window: w, duration: o.duration, curve: o.curve || QEasingCurve.OutCubic, animations: anims };
            if (o.keepAlive !== undefined) args.keepAlive = o.keepAlive;
            if (o.fullScreen) args.fullScreen = true;
            state.ids = animate(args);
        } catch (error) {
            delete w.ascii;
            console.warn("Jarvis ASCII: " + kind + " failed: " + error);
        }
    },

    opened: function (w) {
        if (ascii.debug) console.warn("Jarvis ASCII added " + w.windowClass + " normal=" + ascii.normal(w) + " visible=" + w.visible + " fs=" + effects.hasActiveFullScreenEffect);
        ascii.attach(w);
        var surf = ascii.surface(w);
        if (surf) return ascii.surfaceRun(w, surf, true);
        if (!ascii.normal(w) || !w.visible) return;
        if (!effect.grab(w, Effect.WindowAddedGrabRole, true)) return;
        var strong = ascii.commanded(w, "open");
        ascii.play(w, "open", ascii.MODES.window, {
            role: Effect.WindowAddedGrabRole, strong: strong, from: 0, to: 1,
            duration: strong ? 1150 : ascii.t.open, curve: QEasingCurve.OutCubic,
            extra: [{ type: Effect.Scale, from: { value1: 0.965, value2: 0.95 }, to: { value1: 1, value2: 1 } }]
        });
        w.asciiQuietUntil = Date.now() + 700;
    },
    closed: function (w) {
        var surf = ascii.surface(w);
        if (surf) return ascii.surfaceRun(w, surf, false);
        ascii.stop(w);
        if (!ascii.normal(w) || w.skipsCloseAnimation || w.minimized) return;
        if (!effect.grab(w, Effect.WindowClosedGrabRole, true)) return;
        var strong = ascii.commanded(w, "close");
        ascii.play(w, "close", ascii.MODES.window, {
            role: Effect.WindowClosedGrabRole, strong: strong, from: 1, to: 0, keepAlive: true,
            duration: strong ? 1050 : ascii.t.close, curve: QEasingCurve.OutQuad,
            extra: [{ type: Effect.Scale, from: { value1: 1, value2: 1 }, to: { value1: 0.965, value2: 0.95 } }]
        });
    },
    surfaceRun: function (w, kind, opening) {
        ascii.stop(w);
        if (!opening && w.skipsCloseAnimation) return;
        var role = opening ? Effect.WindowAddedGrabRole : Effect.WindowClosedGrabRole;
        if (!effect.grab(w, role, true)) return;
        var notify = kind === "notify";
        var offset = notify ? { value1: 46, value2: 0 } : { value1: 0, value2: -6 };
        var zero = { value1: 0, value2: 0 };
        ascii.play(w, kind + (opening ? "-in" : "-out"), notify ? ascii.MODES.notify : ascii.MODES.popup, {
            role: role, from: opening ? 0 : 1, to: opening ? 1 : 0, keepAlive: !opening,
            duration: notify ? (opening ? ascii.t.notifyIn : ascii.t.notifyOut) : (opening ? ascii.t.popupIn : ascii.t.popupOut),
            curve: opening ? QEasingCurve.OutQuart : QEasingCurve.OutQuad,
            extra: [{ type: Effect.Translation, from: opening ? offset : zero, to: opening ? zero : offset }]
        });
    },

    dockDelta: function (w) {
        var g = w.geometry, icon = w.iconGeometry, x, y;
        if (icon && icon.width > 0 && icon.height > 0) {
            x = icon.x + icon.width / 2; y = icon.y + icon.height / 2;
        } else {
            var area = effects.virtualScreenGeometry;
            x = area.x + area.width / 2; y = area.y + area.height;
        }
        return { value1: x - g.x - g.width / 2, value2: y - g.y - g.height / 2 };
    },
    minimized: function (w) {
        if (!ascii.normal(w)) return;
        var minimizing = w.minimized;
        var role = minimizing ? Effect.WindowMinimizedGrabRole : Effect.WindowUnminimizedGrabRole;
        if (!effect.grab(w, role, true)) return;
        var strong = ascii.commanded(w, minimizing ? "minimize" : "restore");
        var delta = ascii.dockDelta(w), zero = { value1: 0, value2: 0 };
        var tiny = { value1: 0.08, value2: 0.08 }, one = { value1: 1, value2: 1 };
        ascii.play(w, minimizing ? "minimize" : "restore", ascii.MODES.dock, {
            role: role, strong: strong, from: minimizing ? 1 : 0, to: minimizing ? 0 : 1,
            duration: strong ? 1150 : ascii.t.dock, curve: QEasingCurve.InOutCubic,
            extra: [
                { type: Effect.Scale, from: minimizing ? one : tiny, to: minimizing ? tiny : one },
                { type: Effect.Translation, from: minimizing ? zero : delta, to: minimizing ? delta : zero }
            ]
        });
        if (!minimizing) w.asciiQuietUntil = Date.now() + 700;
    },

    // Window switching: the newly focused window is read once by a glyph band.
    scan: function (w, explicit) {
        if (!w || !ascii.normal(w) || w.minimized || !w.visible) return;
        if (!explicit && (!ascii.focusScan || (w.asciiQuietUntil && Date.now() < w.asciiQuietUntil))) return;
        if (w.ascii && w.ascii.kind !== "scan") return;
        // Quick Alt-Tab: a fast front-loaded band plus a slight settle, so the
        // switch reads instantly even when the switcher never appears.
        ascii.play(w, "scan", ascii.MODES.scan, {
            role: -1, strong: !!explicit, from: 0, to: 1,
            duration: explicit ? 1000 : ascii.t.scan,
            curve: explicit ? QEasingCurve.Linear : QEasingCurve.OutCubic,
            extra: explicit ? [] : [{ type: Effect.Scale, from: { value1: 0.988, value2: 0.988 }, to: { value1: 1, value2: 1 } }]
        });
    },

    geometry: function (w, old) {
        if (!old || !ascii.normal(w) || w.minimized || w.move || w.resize) return;
        var g = w.geometry;
        if (old.width <= 0 || old.height <= 0 || g.width <= 0 || g.height <= 0) return;
        var moved = Math.abs(old.x - g.x) + Math.abs(old.y - g.y);
        var sized = Math.abs(old.width - g.width) + Math.abs(old.height - g.height);
        if (moved + sized < 48) return;
        if (w.ascii && w.ascii.kind !== "scan" && w.ascii.kind !== "geometry") return;
        var strong = ascii.commanded(w, "geometry");
        ascii.play(w, "geometry", ascii.MODES.geometry, {
            role: -1, strong: strong, from: 0, to: 1,
            duration: strong ? 1200 : ascii.t.geometry, curve: QEasingCurve.InOutCubic,
            extra: [
                { type: Effect.Scale, from: { value1: old.width / g.width, value2: old.height / g.height }, to: { value1: 1, value2: 1 } },
                { type: Effect.Translation, from: { value1: old.x + old.width / 2 - g.x - g.width / 2, value2: old.y + old.height / 2 - g.y - g.height / 2 }, to: { value1: 0, value2: 0 } }
            ]
        });
        w.asciiQuietUntil = Date.now() + 500;
    },

    desktopChanged: function (oldDesktop, newDesktop, moving, screen) {
        if (!ascii.desktopSwitch || ascii.reduced || (effects.hasActiveFullScreenEffect && !effect.isActiveFullScreenEffect)) return;
        var order = effects.stackingOrder;
        for (var i = 0; i < order.length; ++i) {
            var w = order[i];
            if (w === moving || (screen !== undefined && w.screen !== screen) || w.minimized ||
                w.desktopWindow || w.dock || w.onAllDesktops || !w.managed) continue;
            var onOld = w.isOnDesktop(oldDesktop), onNew = w.isOnDesktop(newDesktop);
            if (onOld === onNew) continue;
            ascii.play(w, "desktop", ascii.MODES.desktop, {
                role: -1, from: onNew ? 0 : 1, to: onNew ? 1 : 0, keepAlive: false, fullScreen: true,
                duration: ascii.t.desktop, curve: QEasingCurve.OutCubic
            });
        }
    },

    attach: function (w) {
        if (w.asciiAttached) return;
        w.asciiAttached = true;
        w.minimizedChanged.connect(function () { ascii.minimized(w); });
        w.windowFrameGeometryChanged.connect(function (window, old) { ascii.geometry(w, old); });
    },
    ended: function (w) {
        var s = w && w.ascii;
        if (!s) return;
        s.remaining -= 1;
        if (s.remaining <= 0) delete w.ascii;
    },
    changed: function (w, role) {
        if (w.ascii && w.ascii.role === role && effect.isGrabbed(w, role)) ascii.stop(w);
    },
    stopAll: function () {
        effects.stackingOrder.forEach(function (w) { if (w.ascii && w.ascii.kind !== "desktop") ascii.stop(w); });
    }
};

ascii.configure();
try {
    var modes = Object.keys(ascii.MODES);
    for (var i = 0; i < modes.length; ++i) {
        var id = effect.addFragmentShader(Effect.MapTexture | Effect.Modulate, "ascii.frag");
        if (!id) continue;
        effect.setUniform(id, "mode", ascii.MODES[modes[i]]);
        effect.setUniform(id, "strong", 0.0);
        ascii.shaders[ascii.MODES[modes[i]]] = id;
    }
    [ascii.MODES.window, ascii.MODES.dock, ascii.MODES.scan, ascii.MODES.geometry].forEach(function (m) {
        var id = effect.addFragmentShader(Effect.MapTexture | Effect.Modulate, "ascii.frag");
        if (!id) return;
        effect.setUniform(id, "mode", m);
        effect.setUniform(id, "strong", 1.0);
        ascii.shaders[m + "!"] = id;
    });
    console.warn("Jarvis ASCII: " + Object.keys(ascii.shaders).length + " glyph shaders ready");
} catch (error) {
    console.warn("Jarvis ASCII: shader unavailable, opacity fallback: " + error);
}
effects.stackingOrder.forEach(ascii.attach);
effect.configChanged.connect(ascii.configure);
effect.animationEnded.connect(ascii.ended);
effects.windowAdded.connect(ascii.opened);
effects.windowClosed.connect(ascii.closed);
effects.windowDataChanged.connect(ascii.changed);
if (effects.windowActivated) effects.windowActivated.connect(function (w) { ascii.scan(w, false); });
effects.desktopChanged.connect(ascii.desktopChanged);
effects.screenLockingChanged.connect(function (locked) { ascii.locked = locked; ascii.stopAll(); });
effects.activeFullScreenEffectChanged.connect(ascii.stopAll);
