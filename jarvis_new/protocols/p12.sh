#!/usr/bin/env bash
# Fire a protocol command through the bridge, same path as voice/text.
#   p12.sh                     -> initiate protocol 12
#   p12.sh "stand down"        -> any protocol phrase
set -euo pipefail
text="${1:-initiate protocol 12}"
tok="$(cat "$HOME/.jarvis/bridge_token" 2>/dev/null || true)"
curl -s -X POST "http://127.0.0.1:${JARVIS_BRIDGE_PORT:-4317}/route" \
  -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' \
  -d "$(printf '{"text":"%s"}' "$text")"
echo
