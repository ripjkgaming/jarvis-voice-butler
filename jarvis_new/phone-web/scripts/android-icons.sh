#!/usr/bin/env bash
# Rasterise the adaptive-icon layers (docs/phone-app/icon/ic_launcher_*.svg, 108dp) into
# mipmap-*dpi PNGs of the Android project. Outputs are committed; needs inkscape.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../docs/phone-app/icon"
res="$here/../android/app/src/main/res"
for d in mdpi:108 hdpi:162 xhdpi:216 xxhdpi:324 xxxhdpi:432; do
  dir="mipmap-${d%%:*}"; px="${d##*:}"; mkdir -p "$res/$dir"
  for layer in foreground background monochrome; do
    inkscape "$src/ic_launcher_$layer.svg" --export-type=png --export-filename="$res/$dir/ic_launcher_$layer.png" -w "$px" -h "$px" --export-background-opacity=0 2>/dev/null
  done
done
echo "icons written to $res"
