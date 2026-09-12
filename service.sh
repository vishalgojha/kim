#!/usr/bin/env bash
# Management helper for the Kim floating assistant (systemd user service).
# Usage: ./service.sh {install|start|stop|restart|status|logs|uninstall}
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SVC="aurora.service"
SRC="$DIR/systemd/$SVC"
DEST="$HOME/.config/systemd/user/$SVC"
WA_SVC="kim-whatsapp.service"
WA_SRC="$DIR/systemd/$WA_SVC"
WA_DEST="$HOME/.config/systemd/user/$WA_SVC"
PANEL_SVC="kim-panel.service"
PANEL_SRC="$DIR/systemd/$PANEL_SVC"
PANEL_DEST="$HOME/.config/systemd/user/$PANEL_SVC"

case "${1:-}" in
  install)
    mkdir -p "$HOME/.config/systemd/user"
    cp "$SRC" "$DEST"
    cp "$WA_SRC" "$WA_DEST"
    cp "$PANEL_SRC" "$PANEL_DEST"
    if [ -f "$DIR/systemd/aurora-mic.service" ]; then
      cp "$DIR/systemd/aurora-mic.service" "$HOME/.config/systemd/user/"
    fi
    mkdir -p "$HOME/.config/autostart"
    rm -f "$HOME/.config/autostart/kim-panel.desktop"
    systemctl --user daemon-reload
    systemctl --user enable aurora-mic >/dev/null 2>&1 || true
    systemctl --user enable "$WA_SVC"
    systemctl --user enable "$PANEL_SVC"
    systemctl --user enable "$SVC"
    echo "installed. start with: $0 start"
    ;;
  start)
    systemctl --user start aurora-mic 2>/dev/null || true
    systemctl --user start "$WA_SVC"
    systemctl --user start "$PANEL_SVC"
    systemctl --user start "$SVC"
    echo "started."
    ;;
  stop)
    systemctl --user stop "$SVC"
    systemctl --user stop "$WA_SVC" 2>/dev/null || true
    systemctl --user stop "$PANEL_SVC" 2>/dev/null || true
    echo "stopped."
    ;;
  restart)
    $0 stop
    sleep 1
    $0 start
    ;;
  wake)
    if systemctl --user is-active --quiet "$SVC" 2>/dev/null \
       && journalctl --user -u "$SVC" --since '2 min ago' --no-pager 2>/dev/null | grep -q "voice session live"; then
      echo "Kim is already awake and listening."
      exit 0
    fi
    if ! systemctl --user is-active --quiet "$SVC" 2>/dev/null; then
      $0 start
    else
      $0 restart
    fi
    echo -n "waiting for session live..."
    sleep 8
    for i in $(seq 1 100); do
      if journalctl --user -u "$SVC" -n 600 --no-pager 2>/dev/null | grep -q "voice session live"; then
        agent=$(journalctl --user -u "$SVC" -n 600 --no-pager 2>/dev/null | grep "voice session live" | tail -1 | sed -E 's/.*\(agent ([^)]*)\).*/\1/')
      echo " done. Kim is awake (agent ${agent}). Say something!"
        exit 0
      fi
      sleep 1
    done
    echo " not live yet; check 'service.sh logs'."
    ;;
  status)
    systemctl --user status "$SVC" --no-pager -l
    systemctl --user status "$WA_SVC" --no-pager -l
    systemctl --user status "$PANEL_SVC" --no-pager -l
    ;;
  logs)
    journalctl --user -u "$SVC" -n "${2:-80}" --no-pager
    journalctl --user -u "$WA_SVC" -n "${2:-80}" --no-pager
    journalctl --user -u "$PANEL_SVC" -n "${2:-80}" --no-pager
    ;;
  uninstall)
    systemctl --user stop "$SVC" 2>/dev/null || true
    systemctl --user disable "$SVC" 2>/dev/null || true
    systemctl --user stop "$WA_SVC" 2>/dev/null || true
    systemctl --user disable "$WA_SVC" 2>/dev/null || true
    systemctl --user stop "$PANEL_SVC" 2>/dev/null || true
    systemctl --user disable "$PANEL_SVC" 2>/dev/null || true
    rm -f "$DEST"
    rm -f "$WA_DEST"
    rm -f "$PANEL_DEST"
    rm -f "$HOME/.config/autostart/kim-panel.desktop"
    systemctl --user daemon-reload
    echo "uninstalled."
    ;;
  *)
    echo "usage: $0 {install|start|stop|restart|wake|status|logs|uninstall}"
    exit 1
    ;;
esac
