#!/usr/bin/env bash
# Creates a desktop launcher on Linux (and an app-menu entry), or a .command file on macOS.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
chmod +x "$ROOT/start.sh"

if [ "$(uname)" = "Darwin" ]; then
  F="$HOME/Desktop/Lehmus Chat.command"
  printf '#!/bin/bash\n"%s/start.sh"\n' "$ROOT" > "$F"
  chmod +x "$F"
  echo "Created $F"
  exit 0
fi

DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$DESKTOP_DIR" "$HOME/.local/share/applications"
ENTRY="[Desktop Entry]
Type=Application
Name=Lehmus Chat
Comment=Chat with the Lehmus AI models
Exec=\"$ROOT/start.sh\"
Icon=$ROOT/app/static/icon.svg
Terminal=true
Categories=Utility;"
for F in "$DESKTOP_DIR/lehmus-chat.desktop" "$HOME/.local/share/applications/lehmus-chat.desktop"; do
  echo "$ENTRY" > "$F"
  chmod +x "$F"
  command -v gio >/dev/null && gio set "$F" metadata::trusted true 2>/dev/null || true
done
echo "Created $DESKTOP_DIR/lehmus-chat.desktop"
