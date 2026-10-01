# Plasma surface coverage

Audited against this machine's Plasma **6.7.3**, 1 October 2026. Visual tokens
remain those in `design/STARK_OS.md`; the established HUD and existing desktop
frames are unchanged. `build_plasma_extras.py` only adds missing SVG assets.

## Applied through the normal theme installation

| Surface | Theme mechanism | Coverage |
| --- | --- | --- |
| Panel, task buttons, system tray container | Existing `panel-background`, `tasks`, `background` SVGs | Cyan edge indicators, dark glass, chamfers, focus/attention states; panel positions and applet functionality stay native. |
| Kickoff launcher, tray flyouts, notifications, tooltip containers | Existing `dialogs/background`, `tooltip`, list/view item SVGs | STARK panels and selection geometry. Native notification priority, links, dismiss buttons, accessibility and timeout logic are retained. |
| Dialogs, text inputs, buttons, tabs, sliders, scrolling | Existing controls plus new `toolbar`, `line`, `arrows`, `scrollwidget` | Framed dark surfaces and cyan focus/selection/navigation affordances. |
| Checkboxes, radio buttons, switches | New `checkmarks`, `radiobutton`, `switch` | Checked state includes a tick/dot or physical handle position, so colour is not the only signal. Keyboard-focus rings remain distinct. Disabled opacity is controlled by Plasma. |
| Pager / virtual desktop widget | New `pager` | Dark cells, cyan active edge, hover outline. |
| Calendar event markers | New `calendar` | Cyan triangular event mark; event content and dates remain native. |
| Busy indicator / progress | New `busywidget`, `bar_meter_vertical`, existing horizontal meter | Reactor-like arcs and cyan meter. Native busy timing and reduced-motion handling remain controlled by Plasma. |
| Round action controls / overlays | New `actionbutton`, `action-overlays` | Cyan controls, red remove action, original icon meanings retained. |
| Edge highlight effects | New `glowbar` | Restrained cyan frame. |
| Window titlebars | Existing STARK Aurorae decoration | Native window operation targets, dark title strip, cyan focused edge, red destructive hover. |
| Alt+Tab | Native installed `compact` switcher, `kwinrc` → `[TabBox] LayoutName=compact` | Native switcher uses STARK `dialogs/background` and `widgets/viewitem`, system UI font, icons and palette. No custom window-activation code. |
| Lock screen | Native locker + STARK wallpaper + global complementary palette/font | Wallpaper is configurable independently; secure password/fingerprint/session logic remains the distro's native locker. No replacement credential fields. |
| Session startup splash | Existing `org.jarvis.starkos` splash | Existing reactor rings, Orbitron wordmark, progress line. 20/40/60-second ring rotations; animations guarded by visibility and native animation preference. |

## Configuration and integration contract

Run the additive generator **after** the base generator because `build.py`'s
base theme function rebuilds its output tree:

```sh
python3 desktop/build_plasma_extras.py
python3 desktop/verify_plasma_extras.py
```

The root installer integrates the extra generator. It copies these 14 SVGs
alongside the existing `~/.local/share/plasma/desktoptheme/stark-os/widgets/`
assets. No separate Plasma plugin, executable or QML override is needed.

Native switcher selection only needs `kwinrc` `[TabBox] LayoutName=compact`.
A normal compositor configuration reload or next login reads it; restarting
KWin or opening a visible switcher is unnecessary for installation verification.

The lock wallpaper had older JarvisKali artwork in
`[Greeter][Wallpaper][org.kde.image][General]` and older JarvisCommand artwork in
`[Wallpaper][org.kde.image][General]` plus monitor-specific groups. Integration
updates those wallpaper entries to STARK artwork while preserving every
existing lock timeout, authentication and session setting. A native theme may
blur or darken the wallpaper when authentication controls are visible.

## Native and platform-controlled boundaries

- Plasma 6.7's upstream locker QML lives in
  `/usr/share/plasma/shells/org.kde.plasma.desktop/contents/lockscreen/` on this
  machine. Authentication UI layout, secure-attention requirements, fingerprint
  prompts and password masking remain native. STARK does not intercept input,
  draw a replacement password field, or change PAM/polkit configuration.
- SDDM is a separate privileged greeter that does not inherit user Plasma
  settings. This user-level installation does not replace its login theme or
  credentials UI. Firmware, bootloader, early disk-unlock and Plymouth boot
  screens are outside the Plasma user session and unchanged.
- Native compact Alt+Tab receives the theme's geometry. Overview, desktop-grid,
  window thumbnails and other compositor effect layouts remain KWin-owned;
  native colour-aware controls inherit the global palette, whereas effect
  geometry and previews remain native.
- Third-party applets can hardcode colours or load their own QML/SVGs. Their
  outer Plasma containers are themed; internal branding is not guaranteed.
  Tray app icons may be supplied by the application itself.
- Infrequently used legacy Plasma artwork such as analogue clock/meter faces,
  timer artwork, monitor silhouettes and containment-edit handles continues to
  fall back to the installed native theme. Most native monochrome assets use
  Plasma colour-scheme classes and inherit the STARK palette. This preserves
  complete upstream element contracts instead of supplying incomplete assets.
- Native Plasma busy indicators rotate at the platform's own 2-second cycle;
  decorative STARK splash rings keep the design's slower rotations. Native
  animations honour KDE's animation setting; web `prefers-reduced-motion`
  cannot configure a separate desktop process.
- Refresh of cached SVG artwork and existing windows is application-dependent.
  A normal sign-out/sign-in provides complete refresh. No session restarts,
  lockscreen invocation or visible verification apps are required here.

## Verification performed

`verify_plasma_extras.py` verifies all 14 generated SVGs, **141 unique element
IDs**, required native control IDs, positive renderer bounds, each element's
QtSvg rendering and byte-identical generation into a temporary directory.
It forces the Qt offscreen platform and creates no visible windows. Element
contracts were cross-checked against the installed default Plasma SVGs and
native `CheckIndicator.qml`, `RadioIndicator.qml`, `SwitchIndicator.qml` and
`BusyIndicator.qml`. Full interactive lock/unlock and Alt+Tab flows were not
invoked because they would disturb the active session. Native Plasma QML
component instantiation was not included in the pass claim; it depends on live
workspace services. The verified scope is the real QtSvg renderer and installed
native resource contracts.
