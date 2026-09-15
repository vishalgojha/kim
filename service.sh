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
PANEL_DEST="$HOME/.config/systemd/user/$PANEL_SVC"
DESKTOP_SRC="$DIR/systemd/kim.desktop"
DESKTOP_DEST="$HOME/.local/share/applications/kim.desktop"
VOICE_COMMAND="$HOME/.aurora/voice_command"
APP_BIN="$DIR/desktop/src-tauri/target/debug/kim-desktop"

case "${1:-}" in
  install)
    mkdir -p "$HOME/.config/systemd/user"
    cp "$SRC" "$DEST"
    cp "$WA_SRC" "$WA_DEST"
    mkdir -p "$HOME/.local/share/applications"
    # Prefer the packaged Tauri launcher when it exists. The legacy lowercase
    # launcher otherwise creates a second Kim icon in GNOME's app grid.
    if [ -e "/usr/share/applications/Kim.desktop" ]; then
      # Remove the old per-user entry that used the mobile/orb icon and
      # launched the legacy service.sh wrapper.
      rm -f "$HOME/.local/share/applications/Kim.desktop" "$DESKTOP_DEST"
    elif [ ! -e "$HOME/.local/share/applications/Kim.desktop" ]; then
      cp "$DESKTOP_SRC" "$DESKTOP_DEST"
    fi
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
    if [ -f "$DIR/systemd/aurora-mic.service" ]; then
      cp "$DIR/systemd/aurora-mic.service" "$HOME/.config/systemd/user/"
    fi
    mkdir -p "$HOME/.config/autostart"
    rm -f "$HOME/.config/autostart/kim-panel.desktop"
    systemctl --user stop "$PANEL_SVC" 2>/dev/null || true
    systemctl --user disable "$PANEL_SVC" 2>/dev/null || true
    systemctl --user daemon-reload
    systemctl --user stop aurora-mic 2>/dev/null || true
    systemctl --user disable aurora-mic >/dev/null 2>&1 || true
    systemctl --user enable "$WA_SVC"
    systemctl --user enable "$SVC"
    # Keep the local bridge available after login/reboot. Linger is best-effort:
    # some distributions require an administrator to enable it globally.
    loginctl enable-linger "$USER" >/dev/null 2>&1 || true
    systemctl --user start "$WA_SVC"
    systemctl --user start "$SVC"
    echo "installed and started. Kim will start automatically on future logins."
    ;;
  start)
    systemctl --user start "$WA_SVC"
    systemctl --user start "$SVC"
    echo "started."
    ;;
  stop)
    systemctl --user stop "$SVC"
    systemctl --user stop "$WA_SVC" 2>/dev/null || true
    systemctl --user stop aurora-mic 2>/dev/null || true
    systemctl --user stop "$PANEL_SVC" 2>/dev/null || true
    echo "stopped."
    ;;
  restart)
    $0 stop
    sleep 1
    $0 start
    ;;
  open)
    $0 start
    if [ -x "$APP_BIN" ]; then
      nohup "$APP_BIN" >/dev/null 2>&1 &
    elif command -v kim-desktop >/dev/null 2>&1; then
      nohup kim-desktop >/dev/null 2>&1 &
    else
      echo "Kim desktop app is not built. Run: cd desktop && npm run tauri build -- --debug"
      exit 1
    fi
    echo "opened."
    ;;
  wake)
    if ! systemctl --user is-active --quiet "$SVC" 2>/dev/null; then
      $0 start
    fi
    mkdir -p "$(dirname "$VOICE_COMMAND")"
    printf 'wake\n' > "$VOICE_COMMAND"
    echo "wake requested."
    ;;
  pause)
    mkdir -p "$(dirname "$VOICE_COMMAND")"
    printf 'pause\n' > "$VOICE_COMMAND"
    echo "pause requested."
    ;;
  status)
    systemctl --user status "$SVC" --no-pager -l
    systemctl --user status "$WA_SVC" --no-pager -l
    ;;
  logs)
    journalctl --user -u "$SVC" -n "${2:-80}" --no-pager
    journalctl --user -u "$WA_SVC" -n "${2:-80}" --no-pager
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
    rm -f "$DESKTOP_DEST"
    rm -f "$HOME/.config/autostart/kim-panel.desktop"
    systemctl --user daemon-reload
    echo "uninstalled."
    ;;
  *)
    echo "usage: $0 {install|start|stop|restart|open|wake|status|logs|uninstall}"
    exit 1
    ;;
esac
