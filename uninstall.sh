#!/usr/bin/env bash
# Remove o Lyrics Overlay do usuario atual.
#   --purge   apaga tambem cache e configuracao
# O vocabulario salvo (vocabulary.tsv) e dado seu: este script nunca o apaga.
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}"
APP="$DATA/lyrics-overlay"

pkill -f "lyrics_overlay.py" 2>/dev/null || true
if [ -d "$APP" ]; then
  find "$APP" -mindepth 1 -maxdepth 1 ! -name vocabulary.tsv -exec rm -rf {} +
  rmdir "$APP" 2>/dev/null || true   # so remove a pasta se nao sobrou o vocabulario
fi
rm -f "$HOME/.local/bin/lyrics-overlay" \
      "$DATA/applications/lyrics-overlay.desktop" \
      "$DATA/icons/hicolor/scalable/apps/lyrics-overlay.svg" \
      "$CONFIG/autostart/lyrics-overlay.desktop"

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$CONFIG/lyrics-overlay" "$CACHE/lyrics-overlay"
fi

echo "Lyrics Overlay removido."
[ -f "$APP/vocabulary.tsv" ] && echo "Seu vocabulario foi mantido em $APP/vocabulary.tsv" || true
