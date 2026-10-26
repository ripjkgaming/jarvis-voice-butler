#!/usr/bin/env bash
# Install the Locke Brave extension + theme by overriding the Brave launcher so
# every start passes --load-extension. Idempotent. Undo with ./revert.sh.
# Brave cannot silently install unpacked extensions; this is the closest route.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC=/usr/share/applications/brave-browser.desktop
APPS="$HOME/.local/share/applications"
DEST="$APPS/brave-browser.desktop"
MARK="# locke-brave-override (written by desktop/brave/install.sh)"
EXT="$HERE/locke-ext"
THEME="$HERE/locke-theme"

[[ -f "$SRC" ]] || { echo "Missing $SRC; is Brave installed?" >&2; exit 1; }
[[ -f "$EXT/manifest.json" && -f "$THEME/manifest.json" ]] || { echo "locke-ext or locke-theme manifest missing." >&2; exit 1; }
if [[ -f "$DEST" ]] && ! grep -qxF "$MARK" "$DEST"; then
  echo "$DEST exists and was not written by this installer; refusing to overwrite." >&2
  exit 1
fi

mkdir -p "$APPS"
FLAGS="--load-extension=$EXT,$THEME"
{
  echo "$MARK"
  # insert flag right after the binary on every Exec line (always from the system file, so re-runs never stack)
  sed -E "s|^(Exec=[^ ]+)|\1 $FLAGS|" "$SRC"
} > "$DEST.tmp"
mv "$DEST.tmp" "$DEST"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true

echo "Locke Brave override installed: $DEST"
echo "Fully quit Brave (all windows) and start it again from the launcher/taskbar."
echo "Manual fallback: brave://extensions -> Developer mode -> Load unpacked -> pick:"
echo "  $EXT"
echo "  $THEME"
