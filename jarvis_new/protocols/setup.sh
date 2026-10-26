#!/usr/bin/env bash
# One-time setup for Protocol 12 (network scan). Run it yourself, knowingly:
#   bash protocols/setup.sh
#
# Grants nmap raw-socket capability so the scan can fire instantly with no
# password prompt. Nothing here runs unless you answer "y".
set -euo pipefail

nmap_bin="$(command -v nmap || true)"
if [ -z "$nmap_bin" ]; then
  echo "nmap is not installed. Install it first:"
  echo "  sudo dnf install nmap"
  exit 1
fi
nmap_real="$(readlink -f "$nmap_bin")"

echo "nmap: $nmap_real"
echo "Current capabilities: $(getcap "$nmap_real" 2>/dev/null || echo none)"
echo
echo "This will run:"
echo "  sudo setcap cap_net_raw,cap_net_admin,cap_net_bind_service+eip $nmap_real"
echo
echo "Effect: every user on this machine can run nmap's raw-socket scans"
echo "(SYN scan, OS detection) without root. Undo any time with:"
echo "  sudo setcap -r $nmap_real"
echo "Note: a dnf update of nmap replaces the binary; re-run this script after."
echo
read -r -p "Grant nmap raw-socket capability? [y/N] " answer
case "$answer" in
  y|Y|yes|YES) ;;
  *) echo "Cancelled. Nothing changed. Protocol 12 will use pkexec or TCP connect scan."; exit 0 ;;
esac

sudo setcap cap_net_raw,cap_net_admin,cap_net_bind_service+eip "$nmap_real"
echo "Done: $(getcap "$nmap_real")"
echo "Protocol 12 now scans with --privileged and no prompt."
