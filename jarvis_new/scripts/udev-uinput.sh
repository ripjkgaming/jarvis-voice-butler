#!/usr/bin/env bash
# Install the /dev/uinput udev rule (virtual input node used by wtype /
# keyboard tooling). Requires explicit consent: the rule grants the
# "input" group write access to uinput, which is a privilege increase.
#
# Usage:
#   scripts/udev-uinput.sh            # interactive consent prompt
#   scripts/udev-uinput.sh --yes      # non-interactive (wizard-driven)
#   scripts/udev-uinput.sh --check    # report rule state only

set -uo pipefail

RULE='KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"'
RULE_NAME="99-jarvis-uinput.rules"
RULE_DIR="/etc/udev/rules.d"
RULE_PATH="$RULE_DIR/$RULE_NAME"

if [[ "${1:-}" == "--check" ]]; then
  if [[ -f "$RULE_PATH" ]]; then
    echo "OK  $RULE_PATH installed"
    exit 0
  fi
  echo "--  uinput udev rule not installed"
  exit 1
fi

if [[ "${1:-}" != "--yes" ]]; then
  echo
  echo "Jarvis wants to install a udev rule for /dev/uinput."
  echo
  echo "What it does: grants the 'input' group write access to the uinput"
  echo "virtual input node. This lets desktop automation (keyboard typing,"
  echo "media keys) inject input events. It is a privilege increase."
  echo
  echo "Rule contents:"
  echo "  $RULE"
  echo
  read -r -p "Install it? [y/N] " consent
  if [[ ! "$consent" =~ ^[Yy]$ ]]; then
    echo "Skipped. Desktop typing may not work without it."
    exit 0
  fi
fi

echo "== installing $RULE_PATH =="
if ! command -v sudo >/dev/null 2>&1; then
  echo "ERROR: sudo required" >&2
  exit 1
fi
echo "$RULE" | sudo tee "$RULE_PATH" >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger
echo "== done =="