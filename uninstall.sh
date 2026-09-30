#!/usr/bin/env bash
# Remove o Lyrics Overlay do usuario atual. Use --purge para apagar tambem cache e configuracao.
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}"

pkill -f "lyrics_overlay.py" 2>/dev/null || true
rm -rf "$DATA/lyrics-overlay"
rm -f "$HOME/.local/bin/lyrics-overlay" \
      "$DATA/applications/lyrics-overlay.desktop" \
      "$DATA/icons/hicolor/scalable/apps/lyrics-overlay.svg" \
      "$CONFIG/autostart/lyrics-overlay.desktop"

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$CONFIG/lyrics-overlay" "$CACHE/lyrics-overlay"
fi
echo "Lyrics Overlay removido."
