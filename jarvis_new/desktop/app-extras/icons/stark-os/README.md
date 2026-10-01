# STARK OS instrument icons

64 original vector designs; 161 named icons in each of three optical sizes
(16px, 22px, and a detailed scalable master designed on a 128px grid).

The artwork keeps STARK OS geometry and uses an icon-specific light-blue
hologram palette: translucent ice-blue glass, luminous bevel edges, concentric
reactor rings, fine etched details, and distinct silhouettes for folders,
documents, hardware, and applications. Thin dark keylines separate the bright
silhouettes from light backgrounds. The desktop/HUD palette is unchanged.
Semantic warning, error, and success icons retain amber, red, and green.
16/22px variants use brighter blue bodies and larger, simpler forms without
tiny etch marks, retaining their contrast at native toolbar and tray sizes.
All icons have transparent backgrounds; Qt-compatible vector gradients,
paths, and shapes only. No SVG filters, fonts, animation, scripts, or external
resources are used. The system inherits Breeze for unimplemented names.

Common KDE and installed desktop names are covered, including Dolphin,
Konsole, System Settings, KCalc, Spectacle, Ark, Kate, Gwenview, Okular,
KDE Connect, Plasma System Monitor, Brave, VS Code, Discord, OBS, Steam,
LibreOffice, mail, calendar, and generic documents. Third-party familiar
silhouettes are restyled original vector drawings; ChatGPT uses a neutral
conversation symbol. These are theme artwork, not official brand assets.

Theme lookup cannot replace icons embedded in application canvases, browser
websites, sandbox runtimes without the host theme, tray-provided bitmap pixmaps,
or desktop launchers that specify an absolute icon path. Those need an app's
own supported theme mechanism; this generator does not rewrite apps or
security interfaces. Uncovered named icons inherit Breeze rather than a
misleading generic substitute. Cursors are a separate theme.

Regenerate using `python3 desktop/build_icon_extras.py --output PATH`.
The generator writes only `PATH/icons/stark-os`; it never applies settings.
Preview generation is optional and uses CairoSVG and Pillow.
