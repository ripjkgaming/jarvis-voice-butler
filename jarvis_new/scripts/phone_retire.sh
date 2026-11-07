#!/usr/bin/env bash
# JarvisLink 3.x retirement helper (docs/phone-app/RETIRE.md, PLAN.md §15).
#
# DEFAULT = DRY RUN: reads only, writes nothing, changes nothing. It lists
# exactly what each retirement step would archive, remove or leave.
#
#   scripts/phone_retire.sh                 # dry run (R0-R4 report)
#   RETIRE_CONFIRM="retire it" scripts/phone_retire.sh --archive
#        R1 only: copy the APKs to ~/.jarvis/archive/jarvislink/ with
#        SHA256SUMS + README.txt and create the LOCAL tag jarvislink-3.6.1-final.
#        Copies only (originals in ~ stay); never pushes the tag.
#   RETIRE_CONFIRM="retire it" scripts/phone_retire.sh --stage-r2
#        R2 only: `git rm -r phone/` (staged, NOT committed). Refuses unless the
#        R1 archive and tag exist and the branch is not main/master.
#
# Never: commit, push, --force, history rewrites, git clean, deleting untracked
# build output, uninstalling the app from the phone, or touching R3/R4 code
# and config (those are listed for a human/agent to do after the caller audit).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
ARCHIVE="${JARVISLINK_ARCHIVE:-$HOME/.jarvis/archive/jarvislink}"
TAG="jarvislink-3.6.1-final"
mode=dry
case "${1:-}" in
  "") ;;
  --archive) mode=archive ;;
  --stage-r2) mode=r2 ;;
  -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
  *) echo "unknown option: $1" >&2; exit 2 ;;
esac

hr() { printf '\n== %s ==\n' "$1"; }
version="$(grep -m1 'versionName' phone/app/build.gradle* 2>/dev/null | grep -o '"[^"]*"' | tr -d '"' || echo unknown)"
apks=()
for f in "$HOME"/JarvisLink-v*.apk phone/app/build/outputs/apk/*/app-*.apk; do [ -f "$f" ] && apks+=("$f"); done

archive_name() { # JarvisLink-<versionName>.apk
  case "$1" in
    "$HOME"/JarvisLink-v*.apk) b="$(basename "$1" .apk)"; echo "JarvisLink-${b#JarvisLink-v}.apk" ;;
    *) echo "JarvisLink-$version.apk" ;;
  esac
}

need_confirm() {
  if [ "${RETIRE_CONFIRM:-}" != "retire it" ]; then
    echo "Refusing: set RETIRE_CONFIRM=\"retire it\" (the user must have said it; see RETIRE.md R0)." >&2
    exit 3
  fi
}

if [ "$mode" = archive ]; then
  need_confirm
  if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then echo "tag $TAG already exists; not touching it" >&2; exit 1; fi
  [ "${#apks[@]}" -gt 0 ] || { echo "no APKs found; build the 3.x APK first (phone/README.md)" >&2; exit 1; }
  mkdir -p "$ARCHIVE"
  for f in "${apks[@]}"; do cp -n "$f" "$ARCHIVE/$(archive_name "$f")"; done
  (cd "$ARCHIVE" && sha256sum JarvisLink-*.apk > SHA256SUMS)
  commit="$(git rev-parse HEAD)"
  git tag "$TAG" "$commit"
  {
    echo "JarvisLink 3.x archive"
    echo "tag: $TAG -> $commit (local only)"
    echo "restore source: git checkout $TAG -- phone"
    echo "then reinstall an APK below (adb install -r <file>)"
    echo
    cat "$ARCHIVE/SHA256SUMS"
  } > "$ARCHIVE/README.txt"
  echo "archived ${#apks[@]} APK(s) to $ARCHIVE; tagged $TAG at $commit (not pushed)"
  exit 0
fi

if [ "$mode" = r2 ]; then
  need_confirm
  branch="$(git branch --show-current)"
  case "$branch" in main|master|"") echo "refusing on branch '$branch': create a retirement branch first" >&2; exit 1 ;; esac
  git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || { echo "run --archive first (tag $TAG missing)" >&2; exit 1; }
  [ -s "$ARCHIVE/SHA256SUMS" ] || { echo "run --archive first ($ARCHIVE/SHA256SUMS missing)" >&2; exit 1; }
  (cd "$ARCHIVE" && sha256sum -c --quiet SHA256SUMS) || { echo "archive checksum mismatch" >&2; exit 1; }
  git rm -r -q phone/
  echo "staged removal of phone/ ($(git diff --cached --name-only -- phone | wc -l) files). NOT committed."
  echo "Next (same commit): update the references listed by the dry run, then ask the user before committing."
  exit 0
fi

# ------------------------------------------------------------------ dry run
echo "JarvisLink retirement DRY RUN ($(date -Is)) - nothing below is changed."

hr "R0 gate (must all be true before anything else)"
cat <<'EOF'
[ ] Gates P1-P7 green and the RUNBOOK on-phone checklist signed off by the user
[ ] PWA used as the ONLY phone client for 7 consecutive days, no blocker
    (the old APK stays installed as the fallback during those days)
[ ] The user explicitly said "retire it"
EOF

hr "R1 archive (copy only) -> $ARCHIVE"
if [ "${#apks[@]}" -eq 0 ]; then
  echo "  no APKs found (build the 3.x APK once with the gradle command in phone/README.md)"
fi
for f in "${apks[@]}"; do
  printf '  %-60s -> %s\n    sha256 %s\n' "$f" "$(archive_name "$f")" "$(sha256sum "$f" | cut -d' ' -f1)"
done
echo "  + SHA256SUMS, README.txt (tag, restore command, checksums)"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "  tag $TAG already exists -> $(git rev-parse --short "refs/tags/$TAG")"
else
  echo "  would create LOCAL tag $TAG at HEAD $(git rev-parse --short HEAD) (never pushed)"
fi

hr "R2 remove native source: git rm -r phone/ ($(git ls-files phone | wc -l) tracked files, version $version)"
git ls-files phone | sed 's/^/  /'
echo
echo "  untracked build output - NOT deleted by any script, remove by hand if wanted:"
for d in phone/build phone/app/build phone/local.properties phone/.gradle; do
  [ -e "$d" ] && printf '    %-26s %s\n' "$d" "$(du -sh "$d" | cut -f1)"
done
echo
echo "  references to update in the same commit:"
grep -n -E 'phone/|gradle|JarvisLink|RemoteActivity' README.md REMOTE_ACCESS_PLAN.md src/remote/PROTOCOL.md AGENTS.md CLAUDE.md 2>/dev/null \
  | grep -v 'phone-app/' | sed 's/^/    /' | head -60 || true

hr "R3 caller audit (excluding phone/): remove only if the sole callers are its own tests"
audit() { # label, regex
  local hits
  hits="$(grep -rn -E "$2" src frontend shell desktop scripts tests \
    --include='*.py' --include='*.ts' --include='*.tsx' --include='*.rs' --include='*.sh' --include='*.js' \
    --exclude-dir=node_modules --exclude-dir=target --exclude-dir=.next --exclude-dir='*.egg-info' 2>/dev/null \
    | grep -v '^phone/' || true)"
  printf '  %-38s %3d hit(s)\n' "$1" "$(printf '%s' "$hits" | grep -c . || true)"
  printf '%s\n' "$hits" | grep . | sed 's/^/      /' | head -12 || true
}
audit "POST /camera/frame, GET /camera/latest" '"/camera/(frame|latest)"'
audit "POST /type" '"/type"'
audit "POST /talk (keep if wake/shell use it)" '"/talk"'
audit "src/mic_uplink.py + manager.rs" 'mic_uplink|JARVIS_MIC_(PORT|BIND)'
echo "  KEEP (decided in PLAN §15 R3): /summon, /phone/v1/calls (dormant), the {\"guest\":true} stamp,"
echo "  /token /route /chat /tool /mic /room /sys /captions /phone/telemetry /phone/v1/command"
echo "  /session /capabilities, phone_api.py, phone_session.py, mint_token.py"

hr "R4 network tightening (config, with the user's OK; not done by this script)"
echo "  bridge bind now: ${JARVIS_BRIDGE_BIND:-127.0.0.1 (default)}"
command -v tailscale >/dev/null && tailscale serve status 2>/dev/null | sed 's/^/  serve: /' || true
cat <<'EOF2'
  The old APK reaches the bridge only via the tcp://...:4317 serve forward. After R2, with
  the user's OK: remove THAT forward only (verify syntax in `tailscale serve --help`,
  commonly `tailscale serve --tcp=4317 off`; never `tailscale serve reset`), then confirm
  the PWA still works and `nc -vz <host>.ts.net 4317` fails.
EOF2
echo
echo "Dry run complete. No files, refs or settings were changed."
