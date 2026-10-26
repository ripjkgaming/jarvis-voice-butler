#!/usr/bin/env bash
# Remove the Brave launcher override written by install.sh.
set -euo pipefail
APPS="$HOME/.local/share/applications"
DEST="$APPS/brave-browser.desktop"
MARK="# locke-brave-override (written by desktop/brave/install.sh)"
if [[ -f "$DEST" ]] && grep -qxF "$MARK" "$DEST"; then
  rm -f "$DEST"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" || true
  echo "Locke Brave override removed."
elif [[ -f "$DEST" ]]; then
  echo "$DEST was not written by the installer; left untouched."
else
  echo "Nothing to revert."
fi
echo "Restart Brave. Extensions loaded by hand via brave://extensions can be removed there."
