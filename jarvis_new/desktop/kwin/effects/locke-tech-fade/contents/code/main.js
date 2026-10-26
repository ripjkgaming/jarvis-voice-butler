/* SPDX-License-Identifier: MIT */
"use strict";

// All interpolation runs in KWin. No timers, particles or application actions.
var signalFade = {
    shader: 0,
    configure: function () {
        signalFade.openDuration = animationTime(300);
        signalFade.closeDuration = animationTime(220);
        signalFade.reduced = effect.readConfig("ReducedMotion", false);
    },
    eligible: function (window) {
        return !effects.hasActiveFullScreenEffect && window.visible &&
            !window.lockScreen && !window.outline && !window.popupWindow &&
            !window.desktopWindow && !window.dock && !window.fullScreen &&
            window.managed && (window.normalWindow || window.dialog) &&
            !/kscreenlocker|ksmserver|ksplash|jarvis[-_ ]?watch|polkit|kdesu|pinentry|kwallet|spectacle/i.test(window.windowClass) &&
            !(/plasmashell/i.test(window.windowClass) && !window.hasDecoration);
    },
    stop: function (window, key) {
        if (window[key]) {
            cancel(window[key]);
            delete window[key];
        }
    },
    run: function (window, opening) {
        if (!signalFade.eligible(window) || (!opening && window.skipsCloseAnimation)) {
            // A closing window must never keep its unfinished opening animation.
            if (!opening) signalFade.stop(window, "lockeSignalOpen");
            return;
        }
        if (!opening) signalFade.stop(window, "lockeSignalOpen");
        var role = opening ? Effect.WindowAddedGrabRole : Effect.WindowClosedGrabRole;
        // Claim both roles even with motion disabled, preventing another effect
        // from restoring movement. Other effects can still explicitly take over.
        if (!effect.grab(window, role, true)) return;
        var duration = opening ? signalFade.openDuration : signalFade.closeDuration;
        if (duration <= 1 || signalFade.reduced) return;
        var key = opening ? "lockeSignalOpen" : "lockeSignalClose";
        signalFade.stop(window, key);
        var animations = [
            { type: Effect.Scale,
              from: opening ? { value1: 0.975, value2: 0.96 } : { value1: 1, value2: 1 },
              to: opening ? { value1: 1, value2: 1 } : { value1: 0.975, value2: 0.96 } }
        ];
        if (signalFade.shader) {
            animations.push({ type: Effect.ShaderUniform,
                fragmentShader: signalFade.shader, uniform: "reveal",
                from: opening ? 0 : 1, to: opening ? 1 : 0 });
        } else {
            // Unsupported shaders retain a clean native aperture/fade fallback.
            animations[0].from = opening ? { value1: 0.975, value2: 0.16 } : { value1: 1, value2: 1 };
            animations[0].to = opening ? { value1: 1, value2: 1 } : { value1: 0.975, value2: 0.16 };
            animations.push({ type: Effect.Opacity, from: opening ? 0 : 1, to: opening ? 1 : 0 });
        }
        window[key] = animate({window: window, duration: duration,
            curve: opening ? QEasingCurve.OutCubic : QEasingCurve.InOutCubic,
            animations: animations});
    },
    opened: function (window) { signalFade.run(window, true); },
    closed: function (window) { signalFade.run(window, false); },
    changed: function (window, role) {
        if (effect.isGrabbed(window, role)) {
            if (role === Effect.WindowAddedGrabRole) signalFade.stop(window, "lockeSignalOpen");
            if (role === Effect.WindowClosedGrabRole) signalFade.stop(window, "lockeSignalClose");
        }
    }
};
signalFade.configure();
try {
    signalFade.shader = effect.addFragmentShader(Effect.MapTexture | Effect.Modulate, "aperture.frag");
    print("Locke Signal Aperture: holographic shader ready (300/220 ms)");
} catch (error) {
    print("Locke Signal Aperture: native fallback: " + error);
}
effect.configChanged.connect(signalFade.configure);
effects.windowAdded.connect(signalFade.opened);
effects.windowClosed.connect(signalFade.closed);
effects.windowDataChanged.connect(signalFade.changed);
