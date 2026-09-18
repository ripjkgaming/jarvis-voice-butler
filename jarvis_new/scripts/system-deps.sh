#!/usr/bin/env bash
# Jarvis system dependencies installer (Nobara/Fedora dnf + flatpak).
#
# Installs every tool the wizard's deps catalog requires, then prints a
# per-tool checklist. Designed to be re-run safely (dnf + flatpak are
# idempotent). Uses sudo only for the dnf step.
#
# Usage:
#   scripts/system-deps.sh            # install missing dnf + flatpak tools
#   scripts/system-deps.sh --check    # only print the checklist, no install

set -uo pipefail

CHECK_ONLY=0
if [[ "${1:-}" == "--check" ]]; then
  CHECK_ONLY=1
fi

DNF_PACKAGES=(
  spectacle ImageMagick tesseract pipewire-pulse pulseaudio-utils
  playerctl wmctrl wl-clipboard wtype fd-find libnotify nmap nikto gobuster
  upower kde-connect qt qt5-qtbase
)

FLATPAK_APPS=(
  com.brave.Browser
  com.ktechpit.whatsie
  org.vinegarhq.Sober
)

echo "== Jarvis system dependencies =="
echo

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "[check only] resolving binaries:"
  for bin in spectacle convert import tesseract pactl playerctl wmctrl \
             wl-copy wl-paste wtype fdfind fd notify-send nmap nikto gobuster \
             upower kdeconnect-cli qdbus brave-browser; do
    if command -v "$bin" >/dev/null 2>&1; then
      printf "  OK  %-16s %s\n" "$bin" "$(command -v "$bin")"
    else
      printf "  --  %-16s (missing)\n" "$bin"
    fi
  done
  echo
  echo "Flatpak apps:"
  for app in "${FLATPAK_APPS[@]}"; do
    if flatpak info "$app" >/dev/null 2>&1; then
      printf "  OK  %s\n" "$app"
    else
      printf "  --  %s (missing)\n" "$app"
    fi
  done
  exit 0
fi

echo "== dnf packages =="
# Build the list of truly-missing dnf packages.
missing=()
for pkg in "${DNF_PACKAGES[@]}"; do
  if ! rpm -q "$pkg" >/dev/null 2>&1; then
    missing+=("$pkg")
  fi
done
if [[ ${#missing[@]} -eq 0 ]]; then
  echo "  all dnf packages present"
else
  echo "  installing: ${missing[*]}"
  sudo dnf install -y "${missing[@]}" || {
    echo "ERROR: dnf install failed (try again or resolve manually)" >&2
    exit 1
  }
fi

echo
echo "== flatpak apps =="
flatpak remote-info --user flathub >/dev/null 2>&1 || {
  echo "  adding flathub remote (user)..."
  flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo || \
    sudo flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
}
for app in "${FLATPAK_APPS[@]}"; do
  if flatpak info "$app" >/dev/null 2>&1; then
    printf "  OK  %s\n" "$app"
  else
    echo "  installing $app..."
    flatpak install -y --noninteractive flathub "$app" || {
      echo "WARN: flatpak install $app failed (continuing)" >&2
    }
  fi
done

echo
echo "== done =="
echo "Next: run 'python -m wizard run --step sysdeps' or re-open the app wizard."