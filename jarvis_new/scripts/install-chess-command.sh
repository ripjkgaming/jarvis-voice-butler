#!/usr/bin/env bash
# Install the `chess` command: symlink scripts/chess into ~/.local/bin.
# If another program already owns the name `chess`, warn and offer `jchess`.
set -euo pipefail
here="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
target="$here/scripts/chess"
bindir="${CHESS_BIN_DIR:-$HOME/.local/bin}"
name="chess"

if [ "${1:-}" = "--name" ] && [ -n "${2:-}" ]; then
  name="$2"
fi

owner="$(command -v "$name" || true)"
if [ -n "$owner" ] && [ "$(readlink -f "$owner")" != "$target" ]; then
  echo "warning: '$name' already exists on your PATH: $owner" >&2
  if [ "$name" = "chess" ]; then
    echo "  Re-run as: $0 --name jchess   to install it as 'jchess' instead." >&2
    if [ -t 0 ] && [ -z "${CHESS_ASSUME_NO:-}" ]; then
      read -r -p "Install as 'jchess' now? [y/N] " ans
      case "$ans" in
        y|Y) exec "$0" --name jchess ;;
      esac
    fi
  fi
  exit 1
fi

mkdir -p "$bindir"
ln -sf "$target" "$bindir/$name"
echo "installed: $bindir/$name -> $target"
case ":$PATH:" in
  *":$bindir:"*) ;;
  *) echo "note: $bindir is not on your PATH" >&2 ;;
esac
echo "make sure the extras are installed: cd $here && uv sync --extra chess"
echo "(if sherpa-onnx goes missing after a sync: uv pip install sherpa-onnx==1.13.8)"
