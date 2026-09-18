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

# NOTE: only packages that are BOTH missing as an rpm AND missing as a
# binary get installed (see filter below). nikto/gobuster are deliberately
# absent here: they are commonly hand-installed outside rpm (e.g. /usr/local,
# go/bin) and are not in the Fedora repos under those names. pipewire-pulse
# is the metaname — the real rpm is pipewire-pulseaudio.
DNF_PACKAGES=(
  spectacle ImageMagick tesseract pipewire-pulseaudio pulseaudio-utils
  playerctl wmctrl wl-clipboard wtype fd-find libnotify nmap
  upower kde-connect qt qt5-qtbase
)
# rpm name -> binary that already satisfies it when hand-installed.
declare -A PKG_SATISFIED_BY_BIN=(
  [nikto]=nikto
  [gobuster]=gobuster
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
# Build the list of truly-missing dnf packages. A package counts as
# present when its rpm is installed; hand-installed binaries (nikto,
# gobuster) are honored via command -v so dnf never tries to resolve
# names that only exist outside the repos (which aborts the transaction).
missing=()
for pkg in "${DNF_PACKAGES[@]}" "${!PKG_SATISFIED_BY_BIN[@]}"; do
  if rpm -q "$pkg" >/dev/null 2>&1; then
    continue
  fi
  bin="${PKG_SATISFIED_BY_BIN[$pkg]:-}"
  if [[ -n "$bin" ]] && command -v "$bin" >/dev/null 2>&1; then
    echo "  skip $pkg (hand-installed: $(command -v "$bin"))"
    continue
  fi
  # Regular entries have no bin mapping; check a same-named binary as a
  # last resort before asking dnf (catches renames like pipewire-pulse).
  if [[ -z "$bin" ]] && command -v "$pkg" >/dev/null 2>&1; then
    continue
  fi
  missing+=("$pkg")
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
# NOTE: bare `flatpak` commands are ambiguous when flathub exists in both
# system and user installations ("found in multiple installations" error),
# so every command below pins --user explicitly (no sudo needed).
flatpak remotes --user 2>/dev/null | grep -q '^flathub' || {
  echo "  adding flathub remote (user)..."
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
}
for app in "${FLATPAK_APPS[@]}"; do
  if flatpak info --user "$app" >/dev/null 2>&1 || flatpak info --system "$app" >/dev/null 2>&1; then
    printf "  OK  %s\n" "$app"
  else
    echo "  installing $app (user)..."
    flatpak install -y --noninteractive --user flathub "$app" || {
      echo "WARN: flatpak install $app failed (continuing)" >&2
    }
  fi
done

echo
echo "== done =="
echo "Next: run 'python -m wizard run --step sysdeps' or re-open the app wizard."