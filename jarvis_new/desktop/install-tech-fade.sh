#!/usr/bin/env bash
# Install/reload only this effect; never restart KWin or any application.
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE="$HERE/kwin/effects/locke-tech-fade"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
DEST="$DATA/kwin/effects/locke-tech-fade"
mkdir -p "$DATA/jarvis-desktop-backups"
BACKUP="$(mktemp -d "$DATA/jarvis-desktop-backups/tech-aperture-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
cp "$CONFIG/kwinrc" "$BACKUP/kwinrc"
if [ -d "$DEST" ]; then
    cp -a "$DEST" "$BACKUP/locke-tech-fade"
    kpackagetool6 --type KWin/Effect --upgrade "$PACKAGE"
else
    kpackagetool6 --type KWin/Effect --install "$PACKAGE"
fi
# Reconfigure does not reload JavaScript. Reload only this effect's instance.
qdbus org.kde.KWin /Effects org.kde.kwin.Effects.unloadEffect locke-tech-fade
LOADED="$(qdbus org.kde.KWin /Effects org.kde.kwin.Effects.loadEffect locke-tech-fade)"
if [ "$LOADED" != "true" ]; then
    if [ -d "$BACKUP/locke-tech-fade" ]; then
        cp -a "$BACKUP/locke-tech-fade/." "$DEST/"
        qdbus org.kde.KWin /Effects org.kde.kwin.Effects.loadEffect locke-tech-fade
    fi
    echo "Signal Aperture could not load; previous package restored. Backup: $BACKUP" >&2
    exit 1
fi
# Eliminate competing normal-window transitions. Popup and minimize effects stay.
for name in fallapart fade glide scale; do
    kwriteconfig6 --file kwinrc --group Plugins --key "${name}Enabled" false
    qdbus org.kde.KWin /Effects org.kde.kwin.Effects.unloadEffect "$name"
done
kwriteconfig6 --file kwinrc --group Plugins --key locke-tech-fadeEnabled true
qdbus org.kde.KWin /KWin reconfigure
qdbus org.kde.KWin /Effects org.kde.kwin.Effects.reconfigureEffect locke-tech-fade
printf 'Signal Aperture installed. Settings and previous package backup: %s\n' "$BACKUP"
