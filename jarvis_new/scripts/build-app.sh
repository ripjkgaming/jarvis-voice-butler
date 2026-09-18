#!/usr/bin/env bash
# Jarvis desktop build + bundle (phase3): Rust shell -> .rpm + AppImage.
#
# Builds the Tauri shell and bundles rpm + appimage via tauri-bundler.
# The livekit-server sidecar is NOT embedded in the packages: it is
# vendored to ~/.jarvis/bin by the first-run wizard (matching the frozen
# env_cfg.rs contract $JARVIS_LIVEKIT_BIN / ~/.jarvis/bin), keeping the
# .rpm small and arch-portable.
#
# Requires (sudo, once):
#   sudo dnf install -y libayatana-appindicator-gtk3-devel libappindicator-gtk3-devel
#   sudo dnf install -y webkit2gtk4.1-devel gtk3-devel
#
# Usage:
#   scripts/build-app.sh            # release build + bundle
#   scripts/build-app.sh --check    # print what's present/missing

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHELL_DIR="$(cd "$SCRIPT_DIR/../shell" && pwd)"

if [[ "${1:-}" == "--check" ]]; then
  echo "== build deps =="
  for pkg in webkit2gtk4.1-devel gtk3-devel \
             libayatana-appindicator-gtk3-devel libappindicator-gtk3-devel; do
    if rpm -q "$pkg" >/dev/null 2>&1; then
      printf "  OK  %s\n" "$pkg"
    else
      printf "  --  %s (missing: sudo dnf install -y %s)\n" "$pkg" "$pkg"
    fi
  done
  command -v cargo >/dev/null 2>&1 && echo "  OK  cargo" || echo "  --  cargo (missing)"
  command -v cargo-tauri >/dev/null 2>&1 || ls "$HOME/.cargo/bin/cargo-tauri" >/dev/null 2>&1 \
    && echo "  OK  tauri-cli" || echo "  --  tauri-cli (cargo install tauri-cli)"
  exit 0
fi

echo "== tauri build (rpm + appimage) =="
cd "$SHELL_DIR/src-tauri"
# APPIMAGE_EXTRACT_AND_RUN=1 lets linuxdeploy run AppImages in the
# sandboxless path; without it the final AppImage assembly can fail.
APPIMAGE_EXTRACT_AND_RUN=1 NO_STRIP=true \
  cargo tauri build || { echo "ERROR: tauri build failed" >&2; exit 1; }

echo
echo "== bundles =="
ls -lh "$SHELL_DIR/src-tauri/target/release/bundle/rpm/" 2>/dev/null
ls -lh "$SHELL_DIR/src-tauri/target/release/bundle/appimage/" 2>/dev/null

echo
echo "== done =="
echo "Packages are in shell/src-tauri/target/release/bundle/{rpm,appimage}/."
echo "On a fresh machine: install the package, then run the first-run wizard."