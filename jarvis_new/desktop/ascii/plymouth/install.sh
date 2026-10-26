#!/usr/bin/env bash
# Build the Jarvis ASCII Plymouth theme and make it the boot splash.
# Run as your user: it renders frames, then asks sudo to copy them and rebuild
# the initramfs. Undo with ./revert.sh (restores the previous theme).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"
DEST=/usr/share/plymouth/themes/jarvis-ascii
rm -rf "$BUILD"
python3 "$HERE/gen_frames.py" "$BUILD"
cp "$HERE/jarvis-ascii.plymouth" "$HERE/jarvis-ascii.script" "$BUILD/"
PREV="$(plymouth-set-default-theme 2>/dev/null || true)"
sudo bash -c "
  set -e
  [[ -f /var/lib/jarvis-ascii-plymouth.prev ]] || echo '$PREV' > /var/lib/jarvis-ascii-plymouth.prev
  rm -rf '$DEST' && mkdir -p '$DEST' && cp '$BUILD'/* '$DEST/'
  plymouth-set-default-theme -R jarvis-ascii
"
echo "Jarvis ASCII boot installed (was: ${PREV:-unknown}). Preview: sudo plymouthd; sudo plymouth --show-splash; sleep 6; sudo plymouth quit"
