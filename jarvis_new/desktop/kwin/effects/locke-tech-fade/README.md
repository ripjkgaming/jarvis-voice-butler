# Signal Aperture

A coordinated Jarvis-style open/close animation for KWin. Opening assembles the
window from a horizontal cyan scan aperture in 300 ms. Closing collapses the
aperture in 220 ms. A restrained 2.5% horizontal / 4% vertical scale shift gives
it depth without shatter, rotation or spring overshoot. Content is revealed by a
mask rather than squeezed into a strip. Cyan accents disappear before the fully
open state, restoring the original window colors exactly.

KWin owns both interpolations: a scale and a shader uniform. The shader performs
one texture lookup, with no loops, noise, extra textures or persistent rendering.
It follows KWin 6's color-management pipeline. If shader compilation is unavailable,
the effect falls back to native scale/opacity. No process closes, focus changes,
background callbacks or timers are performed by the effect.

```sh
./desktop/install-tech-fade.sh
node desktop/verify-tech-fade.cjs
```

The installer backs up both `kwinrc` and the previous effect package, upgrades and
reloads only this effect, verifies loading, then disables competing normal-window
transitions (`fallapart`, `fade`, `glide`, `scale`). It restores the old package if
loading fails. KWin, applications and services are never restarted. Minimize,
popup, workspace and other effect settings remain as configured.

Global animation scaling applies to both durations, including zero. Explicit
reduced motion disables both transitions while still claiming the animation
roles, preventing competing effects from reintroducing motion:

```sh
kwriteconfig6 --file kwinrc --group Effect-locke-tech-fade --key ReducedMotion true
qdbus org.kde.KWin /Effects org.kde.kwin.Effects.reconfigureEffect locke-tech-fade
```

Excluded: lock screens, watch overlays, desktop/dock surfaces, popups, outlines,
unmanaged/invisible windows, fullscreen windows/effects, session and splash
surfaces, authentication/password/wallet windows, screenshot surfaces, and
undecorated Plasma surfaces. The close-animation opt-out is honored. Closing
cancels an unfinished opening, and another effect taking a grab cancels the
corresponding transition. This effect does not alter watch/security behavior.

Validation on the live KWin 6.7.3 Wayland session: both opening and closing of an
owned temporary Qt window showed Scale plus ShaderUniform animations in KWin's
debug state; no animation remained after completion. At the current global 0.8
factor, observed durations were 240 ms and 176 ms. The focused JavaScript harness
covers both directions, exclusions, disabled/reduced motion, global scaling,
failed grabs, takeover cancellation, rapid closing, and native fallback.
This verifies compositor execution, not a recorded visual-quality review.
