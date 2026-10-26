#!/usr/bin/env bash
# Restore the effects and switcher that install.sh replaced.
set -euo pipefail
STATE="$HOME/.local/share/jarvis-ascii/previous.env"
kwriteconfig6 --file kwinrc --group Plugins --key jarvis-asciiEnabled false
qdbus org.kde.KWin /Effects org.kde.kwin.Effects.unloadEffect jarvis-ascii >/dev/null 2>&1 || true
if [[ -f "$STATE" ]]; then
  while IFS='|' read -r group key value extra; do
    if [[ "$group" == ksplashrc ]]; then
      kwriteconfig6 --file ksplashrc --group "$key" --key "$value" "${extra:-org.jarvis.cinematic}"; continue
    fi
    if [[ -z "$value" ]]; then kwriteconfig6 --file kwinrc --group "$group" --key "$key" --delete
    else kwriteconfig6 --file kwinrc --group "$group" --key "$key" "$value"; fi
  done < "$STATE"
  rm -f "$STATE"
fi
for e in locke-tech-fade jarvis-surfaces slidingpopups slide; do
  [[ "$(kreadconfig6 --file kwinrc --group Plugins --key ${e}Enabled)" == "true" ]] && \
    qdbus org.kde.KWin /Effects org.kde.kwin.Effects.loadEffect "$e" >/dev/null 2>&1 || true
done
qdbus org.kde.KWin /Effects org.kde.kwin.Effects.reconfigureEffect jarvis-energy >/dev/null 2>&1 || true
qdbus org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
echo "Jarvis ASCII transitions reverted."
