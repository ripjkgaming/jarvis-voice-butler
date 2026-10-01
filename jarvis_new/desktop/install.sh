#!/usr/bin/env bash
# STARK OS: give the whole KDE Plasma 6 desktop the Jarvis HUD look.
#
# User-level only (no sudo). Backs up every config it touches to
# ~/.jarvis/desktop-backup/<timestamp>/ first; desktop/revert.sh restores the
# newest backup. Re-running is safe: it re-installs and re-applies.
#
#   desktop/install.sh                 # install + apply everything
#   desktop/install.sh --no-wallpaper  # keep the current wallpapers
#
# Never restarts KWin (that would end the Wayland session) and never touches
# panel geometry or applet layout: the Jarvis school-mode taskbar docks over
# the bottom panel exactly where it is.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$HERE")"
CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
QDBUS="$(command -v qdbus6 || command -v qdbus-qt6 || command -v qdbus || true)"

WALLPAPER=1
for arg in "$@"; do
    case "$arg" in
        --no-wallpaper) WALLPAPER=0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\033[38;2;95;227;255m▸\033[0m %s\n' "$*"; }
kw() { kwriteconfig6 --notify "$@"; }

plasma_script() {
    [ -n "$QDBUS" ] || return 1
    "$QDBUS" org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "$1"
}

# Fail before changing anything when run outside a Plasma 6 session.
for cmd in kwriteconfig6 kreadconfig6 plasma-apply-colorscheme plasma-apply-desktoptheme fc-cache python3; do
    command -v "$cmd" >/dev/null || { echo "Required command missing: $cmd" >&2; exit 1; }
done
plasma_script 'print("STARK OS ready");' >/dev/null || {
    echo "Run this installer from your logged-in KDE Plasma session." >&2
    exit 1
}
for file in colors/StarkOS.colors plasma/desktoptheme/stark-os/metadata.json \
    aurorae/stark-os/decoration.svg look-and-feel/org.jarvis.starkos/contents/splash/Splash.qml \
    wallpapers/stark-os-sector-01.png wallpapers/stark-os-sector-02.png konsole/StarkOS.profile \
    app-extras/themes/StarkOS/gtk-3.0/gtk.css app-extras/icons/stark-os/index.theme \
    app-extras/icons/stark-os-cursors/index.theme; do
    [ -s "$HERE/$file" ] || { echo "Missing asset: $file; run desktop/build.py --wallpaper first." >&2; exit 1; }
done

# ---------------------------------------------------------------- backup
mkdir -p "$HOME/.jarvis/desktop-backup"
BACKUP="$(mktemp -d "$HOME/.jarvis/desktop-backup/$(date +%Y%m%d-%H%M%S)-XXXXXX")"
chmod 700 "$BACKUP"
say "Backing up current desktop settings to $BACKUP"
trap 'echo "Installation failed. Restore saved settings with: $HERE/revert.sh $BACKUP" >&2' ERR
python3 "$HERE/integration.py" backup "$BACKUP"
for f in kdeglobals kwinrc plasmarc konsolerc kcminputrc ksplashrc \
         plasma-org.kde.plasma.desktop-appletsrc plasmashellrc; do
    if [ -f "$CFG/$f" ]; then cp -a "$CFG/$f" "$BACKUP/"; else touch "$BACKUP/$f.absent"; fi
done
if [ -d "$DATA/konsole" ]; then cp -a "$DATA/konsole" "$BACKUP/konsole"; fi
plasma_script '
var out = [];
desktops().forEach(function (d) {
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    out.push({id: d.id, plugin: d.wallpaperPlugin, image: d.readConfig("Image", ""), fillMode: d.readConfig("FillMode", 2)});
});
print(JSON.stringify(out));' > "$BACKUP/wallpapers.json"
python3 -m json.tool "$BACKUP/wallpapers.json" >/dev/null

# ---------------------------------------------------------------- install
say "Installing fonts"
mkdir -p "$DATA/fonts/stark-os"
cp "$REPO/design/fonts/"*.ttf "$REPO/design/fonts/"*OFL.txt "$DATA/fonts/stark-os/"
fc-cache -f "$DATA/fonts" >/dev/null

say "Installing colour scheme, Plasma theme, window decoration, splash, Konsole"
mkdir -p "$DATA/color-schemes" "$DATA/plasma/desktoptheme" "$DATA/aurorae/themes" \
         "$DATA/plasma/look-and-feel" "$DATA/wallpapers/StarkOS" "$DATA/konsole"
cp "$HERE/colors/StarkOS.colors" "$DATA/color-schemes/"
rm -rf "$DATA/plasma/desktoptheme/stark-os" "$DATA/aurorae/themes/stark-os" \
       "$DATA/plasma/look-and-feel/org.jarvis.starkos"
cp -r "$HERE/plasma/desktoptheme/stark-os" "$DATA/plasma/desktoptheme/"
cp -r "$HERE/aurorae/stark-os" "$DATA/aurorae/themes/"
cp -r "$HERE/look-and-feel/org.jarvis.starkos" "$DATA/plasma/look-and-feel/"
cp "$HERE/wallpapers/"*.png "$DATA/wallpapers/StarkOS/"
cp "$HERE/konsole/StarkOS.colorscheme" "$HERE/konsole/StarkOS.profile" "$DATA/konsole/"
# Stale rendered SVGs would hide a re-install's changes.
rm -f "${XDG_CACHE_HOME:-$HOME/.cache}/plasma_theme_stark-os"*.kcache

# ---------------------------------------------------------------- apply
say "Fonts: Rajdhani for the interface, Share Tech Mono for fixed width"
# Ten-field QFont strings use legacy weights: 57=Medium, 63=DemiBold,
# 50=Normal. Writing 500/600/400 here is interpreted as Black by Qt 6.
kw --file kdeglobals --group General --key font "Rajdhani,11,-1,5,57,0,0,0,0,0"
kw --file kdeglobals --group General --key menuFont "Rajdhani,11,-1,5,57,0,0,0,0,0"
kw --file kdeglobals --group General --key toolBarFont "Rajdhani,11,-1,5,57,0,0,0,0,0"
kw --file kdeglobals --group General --key smallestReadableFont "Rajdhani,9,-1,5,57,0,0,0,0,0"
kw --file kdeglobals --group General --key fixed "Share Tech Mono,10,-1,5,50,0,0,0,0,0"
kw --file kdeglobals --group WM --key activeFont "Rajdhani,11,-1,5,63,0,0,0,0,0"

say "Colour scheme: Stark OS"
if [ "$(kreadconfig6 --file kdeglobals --group General --key ColorScheme)" = "StarkOS" ]; then
    plasma-apply-colorscheme BreezeDark >/dev/null 2>&1 || true
fi
plasma-apply-colorscheme StarkOS

say "Plasma theme: stark-os"
if [ "$(kreadconfig6 --file plasmarc --group Theme --key name)" = "stark-os" ]; then
    plasma-apply-desktoptheme breeze-dark >/dev/null 2>&1 || true
    sleep 1
fi
plasma-apply-desktoptheme stark-os

say "Window decoration: Stark OS (Aurorae)"
kw --file kwinrc --group org.kde.kdecoration2 --key library org.kde.kwin.aurorae
kw --file kwinrc --group org.kde.kdecoration2 --key theme __aurorae__svg__stark-os
kw --file kwinrc --group org.kde.kdecoration2 --key ButtonsOnLeft ""
kw --file kwinrc --group org.kde.kdecoration2 --key ButtonsOnRight "IAX"

say "Splash screen + look-and-feel id"
kw --file ksplashrc --group KSplash --key Engine KSplashQML
kw --file ksplashrc --group KSplash --key Theme org.jarvis.starkos
kw --file kdeglobals --group KDE --key LookAndFeelPackage org.jarvis.starkos

say "Konsole: Stark OS profile"
kw --file konsolerc --group "Desktop Entry" --key DefaultProfile StarkOS.profile

say "Application controls, GTK, icons, cursors, native task switcher and lock wallpaper"
python3 "$HERE/integration.py" apply

if [ "$WALLPAPER" = 1 ]; then
    say "Wallpapers: sector 01 left screen, sector 02 right screen"
    # JSON strings safely quote file URLs, including spaces and apostrophes.
    W1="$(python3 -c 'import json,pathlib,sys; print(json.dumps(pathlib.Path(sys.argv[1]).resolve().as_uri()))' "$DATA/wallpapers/StarkOS/stark-os-sector-01.png")"
    W2="$(python3 -c 'import json,pathlib,sys; print(json.dumps(pathlib.Path(sys.argv[1]).resolve().as_uri()))' "$DATA/wallpapers/StarkOS/stark-os-sector-02.png")"
    plasma_script "
desktops().forEach(function (d) {
    var img = $W1;
    if (d.screen >= 0 && screenGeometry(d.screen).x > 0) img = $W2;
    d.wallpaperPlugin = 'org.kde.image';
    d.currentConfigGroup = ['Wallpaper', 'org.kde.image', 'General'];
    d.writeConfig('Image', img);
    d.writeConfig('FillMode', 2);
});" >/dev/null
fi

say "Reloading KWin settings (no restart)"
if [ -n "$QDBUS" ]; then "$QDBUS" org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true; fi
python3 "$HERE/integration.py" verify

say "STARK OS online. Backup: $BACKUP  ·  Revert: desktop/revert.sh"
