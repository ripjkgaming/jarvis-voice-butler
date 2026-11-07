#!/usr/bin/env bash
# LOCKE//LINK deploy (docs/phone-app/PLAN.md §13). Builds phone-web/, copies
# dist/ to ~/.jarvis/phone-web/releases/<BUILD_ID>/ and atomically swaps the
# `current` symlink. Keeps the newest 3 releases.
#
#   scripts/phone_web_deploy.sh               # check + build + release + swap
#   scripts/phone_web_deploy.sh --skip-check  # build only (no tsc/vitest/playwright)
#   scripts/phone_web_deploy.sh --serve       # also ensure the tailscale serve mounts
#   scripts/phone_web_deploy.sh --rollback    # point `current` at the previous release
#   scripts/phone_web_deploy.sh --status      # show releases and the serve config
#
# Network exposure changes ONLY with --serve. Verified CLI syntax on this host
# (tailscale `serve --help`, 2026-11-07): `tailscale serve --bg --set-path <path> <target>`
# where <target> is a directory, or an http:// URL (the mount path is stripped
# before proxying; the bridge accepts /api either way). Tailnet only. Never funnel.
#   tailscale serve --bg --set-path /      ~/.jarvis/phone-web/current
#   tailscale serve --bg --set-path /api   http://127.0.0.1:4317
#   tailscale serve --bg --set-path /voice http://127.0.0.1:7880/voice
# Prerequisite: HTTPS certificates enabled for the tailnet (admin console → DNS →
# HTTPS Certificates). On 2026-11-07 `tailscale status --json` reported
# CertDomains = null, i.e. NOT enabled yet; see docs/phone-app/RUNBOOK.md.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB="$REPO/phone-web"
HOME_DIR="${PHONE_WEB_HOME:-$HOME/.jarvis/phone-web}"
KEEP=3
check=1
serve=0
mode=deploy
direct=0
for arg in "$@"; do
  case "$arg" in
    --skip-check) check=0 ;;
    --serve) serve=1 ;;
    --serve-direct) serve=1; direct=1 ;; # if tailscaled will not follow the symlink
    --rollback) mode=rollback ;;
    --status) mode=status ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$HOME_DIR/releases"
cd "$HOME_DIR"

current_id() { [ -L current ] && basename "$(readlink current)" || true; }

swap_to() { # atomic: a new symlink renamed over the old one
  ln -sfn "releases/$1" current.tmp
  mv -T current.tmp current
  echo "current -> releases/$1"
}

apply_serve() {
  local root="$HOME_DIR/current"
  [ "$direct" = 1 ] && root="$(readlink -f "$HOME_DIR/current")"
  tailscale serve --bg --set-path / "$root"
  tailscale serve --bg --set-path /api http://127.0.0.1:4317
  tailscale serve --bg --set-path /voice http://127.0.0.1:7880/voice
  tailscale serve status
}

case "$mode" in
  status)
    echo "releases (newest first):"
    ls -1t releases 2>/dev/null | sed 's/^/  /'
    echo "current: $(current_id)"
    command -v tailscale >/dev/null && tailscale serve status || true
    exit 0
    ;;
  rollback)
    cur="$(current_id)"
    prev="$(ls -1t releases | grep -vx "$cur" | head -1 || true)"
    [ -n "$prev" ] || { echo "no previous release to roll back to" >&2; exit 1; }
    swap_to "$prev"
    if [ "$serve" = 1 ]; then apply_serve; fi
    exit 0
    ;;
esac

cd "$WEB"
pnpm install --frozen-lockfile
if [ "$check" = 1 ]; then
  pnpm check # tsc, vitest, build, size budgets, playwright
fi
LINK_BUILD_ID="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo nogit)-$(date +%Y%m%d%H%M%S)"
export LINK_BUILD_ID
pnpm build
id="$LINK_BUILD_ID"
grep -q "content=\"$id\"" dist/index.html || { echo "BUILD_ID not stamped into dist/index.html" >&2; exit 1; }

cd "$HOME_DIR"
rm -rf "releases/$id.tmp"
cp -a "$WEB/dist" "releases/$id.tmp"
mv "releases/$id.tmp" "releases/$id"
touch "releases/$id"
swap_to "$id"

# keep the newest $KEEP releases, never the one `current` points at
cur="$(current_id)"
{ ls -1t releases | grep -vx "$cur" || true; } | tail -n +"$KEEP" | while read -r old; do
  rm -rf "releases/$old"
  echo "pruned $old"
done

if [ "$serve" = 1 ]; then apply_serve; fi
echo "deployed $id"
