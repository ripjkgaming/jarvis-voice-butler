#!/usr/bin/env bash
# Restore the Plymouth theme that install.sh replaced and rebuild the initramfs.
set -euo pipefail
sudo bash -c '
  prev=$(cat /var/lib/jarvis-ascii-plymouth.prev 2>/dev/null || echo jarvis-boot)
  plymouth-set-default-theme -R "${prev:-jarvis-boot}"
  rm -f /var/lib/jarvis-ascii-plymouth.prev
  echo "restored Plymouth theme: ${prev:-jarvis-boot}"
'
