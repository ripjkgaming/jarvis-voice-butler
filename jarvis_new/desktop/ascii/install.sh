#!/usr/bin/env bash
# Install Jarvis ASCII transitions: KWin effect + Alt-Tab switcher.
# Disables the effects it replaces; jarvis-energy stays on for window dragging.
# Undo with ./revert.sh.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/kwin"
STATE="$HOME/.local/share/jarvis-ascii"
mkdir -p "$DEST/effects" "$DEST/tabbox" "$STATE"
rm -rf "$DEST/effects/jarvis-ascii" "$DEST/tabbox/jarvis-ascii"
cp -r "$HERE/kwin/effects/jarvis-ascii" "$DEST/effects/"
cp -r "$HERE/kwin/tabbox/jarvis-ascii" "$DEST/tabbox/"
LNF="$HOME/.local/share/plasma/look-and-feel"
mkdir -p "$LNF" && rm -rf "$LNF/org.jarvis.ascii" && cp -r "$HERE/look-and-feel/org.jarvis.ascii" "$LNF/"

kr() { kreadconfig6 --file kwinrc --group "$1" --key "$2" 2>/dev/null || true; }
kw() { kwriteconfig6 --file kwinrc --group "$1" --key "$2" "$3"; }

# remember what we change, once
if [[ ! -f "$STATE/previous.env" ]]; then
  {
    for k in locke-tech-fadeEnabled jarvis-surfacesEnabled slidingpopupsEnabled slideEnabled fadingpopupsEnabled; do
      echo "Plugins|$k|$(kr Plugins $k)"
    done
    echo "TabBox|LayoutName|$(kr TabBox LayoutName)"
    echo "TabBox|DelayTime|$(kr TabBox DelayTime)"
    echo "Effect-jarvis-energy|DragOnly|$(kr Effect-jarvis-energy DragOnly)"
    echo "ksplashrc|KSplash|Theme|$(kreadconfig6 --file ksplashrc --group KSplash --key Theme)"
  } > "$STATE/previous.env"
fi

kw Plugins jarvis-asciiEnabled true
for k in locke-tech-fadeEnabled jarvis-surfacesEnabled slidingpopupsEnabled slideEnabled fadingpopupsEnabled; do
  kw Plugins "$k" false
done
kw Effect-jarvis-energy DragOnly true
kw TabBox LayoutName jarvis-ascii
# quick Alt-Tab taps switch without flashing the switcher
kw TabBox DelayTime 120
kwriteconfig6 --file ksplashrc --group KSplash --key Engine KSplashQML
kwriteconfig6 --file ksplashrc --group KSplash --key Theme org.jarvis.ascii

q() { qdbus org.kde.KWin /Effects "org.kde.kwin.Effects.$1" "$2" >/dev/null 2>&1 || true; }
for e in locke-tech-fade jarvis-surfaces slidingpopups slide fadingpopups; do q unloadEffect "$e"; done
q reconfigureEffect jarvis-energy
q unloadEffect jarvis-ascii
q loadEffect jarvis-ascii
qdbus org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
echo "Jarvis ASCII transitions installed."
