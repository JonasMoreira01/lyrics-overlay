#!/usr/bin/env bash
# Instala o Lyrics Overlay para o usuario atual (sem sudo).
#   ./install.sh               instalacao completa (inclui tradutor offline, ~200MB)
#   ./install.sh --lite        sem tradutor offline (traducao cai no Google, que pode bloquear)
#   ./install.sh --autostart   tambem inicia com a sessao
set -euo pipefail

LITE=0
AUTOSTART=0
for arg in "$@"; do
  case "$arg" in
    --lite) LITE=1 ;;
    --autostart) AUTOSTART=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Opcao desconhecida: $arg" >&2; exit 1 ;;
  esac
done

SRC="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$DATA/lyrics-overlay"
BIN="$HOME/.local/bin"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"

# --- dependencias do sistema (nao instalamos com sudo: so avisamos)
if ! python3 -c 'import gi; gi.require_version("Gtk","3.0"); import cairo' 2>/dev/null; then
  echo "Faltam dependencias do sistema. No Ubuntu/Debian/Mint:" >&2
  echo "  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-venv" >&2
  exit 1
fi
if ! python3 -c 'import gi
try:
    gi.require_version("AyatanaAppIndicator3","0.1")
except ValueError:
    gi.require_version("AppIndicator3","0.1")' 2>/dev/null; then
  echo "Aviso: sem AppIndicator o icone de bandeja pode nao aparecer." >&2
  echo "  sudo apt install gir1.2-ayatanaappindicator3-0.1" >&2
fi
if [ "${XDG_SESSION_TYPE:-x11}" = "wayland" ]; then
  echo "Aviso: Wayland detectado. O overlay (sempre no topo / posicao) foi feito para X11 e pode nao funcionar direito." >&2
fi

# --- arquivos do app
mkdir -p "$APP" "$BIN" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
cp "$SRC/lyrics_overlay.py" "$APP/"
mkdir -p "$APP/icons" && cp "$SRC"/icons/*.svg "$APP/icons/"
cp "$SRC/icons/lyrics-overlay.svg" "$DATA/icons/hicolor/scalable/apps/lyrics-overlay.svg"

# --- ambiente Python (enxerga o PyGObject do sistema)
python3 -m venv --system-site-packages "$APP/venv"
PIP_OPTS=(-q --no-warn-conflicts --disable-pip-version-check)
"$APP/venv/bin/pip" install "${PIP_OPTS[@]}" requests
if [ "$LITE" -eq 0 ]; then
  echo "Instalando tradutor offline (download de ~200MB)..."
  "$APP/venv/bin/pip" install "${PIP_OPTS[@]}" argostranslate
  "$APP/venv/bin/python" - <<'PY'
import argostranslate.package as p
installed = {(l.from_code, l.to_code) for l in p.get_installed_packages()}
if ("en", "pt") not in installed:
    p.update_package_index()
    pkg = next(x for x in p.get_available_packages() if x.from_code == "en" and x.to_code == "pt")
    p.install_from_path(pkg.download())
print("Modelo EN->PT pronto.")
PY
fi

# --- lancador e atalho de menu
cat > "$BIN/lyrics-overlay" <<EOF
#!/usr/bin/env bash
exec "$APP/venv/bin/python" "$APP/lyrics_overlay.py" "\$@"
EOF
chmod +x "$BIN/lyrics-overlay"

sed "s|@EXEC@|$BIN/lyrics-overlay|" "$SRC/lyrics-overlay.desktop.in" > "$DATA/applications/lyrics-overlay.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true

if [ "$AUTOSTART" -eq 1 ]; then
  mkdir -p "$CONFIG/autostart"
  cp "$DATA/applications/lyrics-overlay.desktop" "$CONFIG/autostart/lyrics-overlay.desktop"
  echo "Autostart ativado."
fi

echo
echo "Pronto. Abra 'Lyrics Overlay' no menu de aplicativos (ou rode: lyrics-overlay)."
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Obs.: $BIN nao esta no PATH; use o menu de aplicativos ou o caminho completo." ;; esac
