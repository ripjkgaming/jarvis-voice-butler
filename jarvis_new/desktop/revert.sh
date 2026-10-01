#!/usr/bin/env bash
# Undo desktop/install.sh: restore the newest backup in ~/.jarvis/desktop-backup/
# (or the one given as $1). The installed theme files stay in ~/.local/share
# (harmless, selectable again in System Settings).
#
#   desktop/revert.sh
#   desktop/revert.sh ~/.jarvis/desktop-backup/20261001-200000
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CFG="${XDG_CONFIG_HOME:-$HOME/.config}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
QDBUS="$(command -v qdbus6 || command -v qdbus-qt6 || command -v qdbus || true)"
BACKUP="${1:-$(find "$HOME/.jarvis/desktop-backup" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -1 || true)}"
BACKUP="${BACKUP%/}"
[ -d "$BACKUP" ] || { echo "no backup found in ~/.jarvis/desktop-backup" >&2; exit 1; }
echo "Restoring desktop settings from $BACKUP"
if [ -f "$BACKUP/icon-update.json" ]; then
    python3 "$HERE/integration.py" restore-icons "$BACKUP"
    echo "Icon artwork restored. Other desktop settings were not changed."
    exit 0
fi
python3 "$HERE/integration.py" restore "$BACKUP"

old() { kreadconfig6 --file "$BACKUP/$1" --group "$2" --key "$3" 2>/dev/null || true; }

scheme="$(old kdeglobals General ColorScheme)"
theme="$(old plasmarc Theme name)"
plasma-apply-colorscheme "${scheme:-BreezeLight}"
plasma-apply-desktoptheme "${theme:-default}"

# Fonts and window-manager keys come back live (with change notification).
for key in font menuFont toolBarFont smallestReadableFont fixed; do
    val="$(old kdeglobals General "$key")"
    if [ -n "$val" ]; then
        kwriteconfig6 --notify --file kdeglobals --group General --key "$key" "$val"
    else
        kwriteconfig6 --notify --file kdeglobals --group General --key "$key" --delete
    fi
done
val="$(old kdeglobals WM activeFont)"
if [ -n "$val" ]; then
    kwriteconfig6 --notify --file kdeglobals --group WM --key activeFont "$val"
else
    kwriteconfig6 --notify --file kdeglobals --group WM --key activeFont --delete
fi
lnf="$(old kdeglobals KDE LookAndFeelPackage)"
if [ -n "$lnf" ]; then
    kwriteconfig6 --notify --file kdeglobals --group KDE --key LookAndFeelPackage "$lnf"
else
    kwriteconfig6 --notify --file kdeglobals --group KDE --key LookAndFeelPackage --delete
fi

# Whole files that nothing else rewrites while the session runs.
for f in kwinrc ksplashrc konsolerc kcminputrc; do
    if [ -f "$BACKUP/$f" ]; then
        cp -a "$BACKUP/$f" "$CFG/$f"
    elif [ -f "$BACKUP/$f.absent" ]; then
        rm -f "$CFG/$f"
    fi
done
if [ -d "$BACKUP/konsole" ]; then
    mkdir -p "$DATA/konsole"
    cp -a "$BACKUP/konsole/." "$DATA/konsole/"
fi

# Wallpapers, per desktop, as they were.
if { [ -s "$BACKUP/wallpapers.json" ] || [ -s "$BACKUP/wallpapers.tsv" ]; } && [ -n "$QDBUS" ]; then
    # Keep compatibility with the original Claude backup while safely encoding
    # quotes, backslashes and tabs in file paths. New backups also save FillMode.
    saved="$(python3 - "$BACKUP" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
if (root / 'wallpapers.json').exists():
    records = json.loads((root / 'wallpapers.json').read_text())
else:
    records = []
    for row in (root / 'wallpapers.tsv').read_text().splitlines():
        fields = row.split('\t', 2)
        if len(fields) == 3:
            records.append(dict(zip(('id', 'plugin', 'image'), fields)))
print(json.dumps({str(item['id']): item for item in records}))
PY
)"
    js="var saved = $saved;
desktops().forEach(function (d) {
    var s = saved[d.id];
    if (!s) return;
    d.wallpaperPlugin = s.plugin;
    d.currentConfigGroup = ['Wallpaper', 'org.kde.image', 'General'];
    d.writeConfig('Image', s.image);
    if (s.fillMode !== undefined) d.writeConfig('FillMode', s.fillMode);
});"
    "$QDBUS" org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "$js" >/dev/null
fi

if [ -n "$QDBUS" ]; then "$QDBUS" org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true; fi
echo "Done. Apps already open pick up the old fonts when restarted."
