#!/usr/bin/env bash
# RustDesk control for the Jarvis "remote session" voice tool.
# The phone connects over Tailscale with RustDesk direct IP access (21118).
# Usage: remote_session.sh {start|stop|status}
# Prints a single JSON object to stdout. Exit 0 on ok.
set -u
APP="com.rustdesk.RustDesk"
PORT=21118
LOG_DIR="$HOME/.jarvis/remote"

json_safe() { head -c 200 | tr -d '\n"\\'; }

is_running() { pgrep -x rustdesk >/dev/null 2>&1; }

case "${1:-}" in
  start)
    if ! flatpak info --user "$APP" >/dev/null 2>&1; then
      printf '{"ok":false,"error":"RustDesk not installed: flatpak install --user flathub %s"}\n' "$APP"
      exit 1
    fi
    if ! is_running; then
      mkdir -p "$LOG_DIR"
      setsid flatpak run --user "$APP" --tray >"$LOG_DIR/rustdesk.log" 2>&1 < /dev/null &
      for _ in $(seq 1 20); do
        is_running && break
        sleep 0.5
      done
    fi
    if ! is_running; then
      why=$(tail -n 1 "$LOG_DIR/rustdesk.log" 2>/dev/null | json_safe)
      printf '{"ok":false,"error":"RustDesk did not start: %s"}\n' "${why:-unknown}"
      exit 1
    fi
    printf '{"ok":true,"running":true,"app":"rustdesk","port":%s}\n' "$PORT"
    exit 0
    ;;
  stop)
    flatpak kill "$APP" >/dev/null 2>&1 || true
    printf '{"ok":true,"running":false}\n'
    exit 0
    ;;
  status)
    if is_running; then
      printf '{"ok":true,"running":true,"port":%s}\n' "$PORT"
    else
      printf '{"ok":true,"running":false}\n'
    fi
    exit 0
    ;;
  *)
    printf '{"ok":false,"error":"usage: remote_session.sh {start|stop|status}"}\n'
    exit 1
    ;;
esac
