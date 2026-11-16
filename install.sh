#!/usr/bin/env sh
# Installiert JARVIS für den aktuellen Benutzer unter macOS und Linux.
#
#   ./install.sh                 installieren oder aktualisieren
#   ./install.sh --extras voice  mit Spracherkennung und Vorlesen
#   ./install.sh --uninstall     Programm entfernen, Daten behalten
set -eu

SOURCE=$(cd "$(dirname "$0")" && pwd)
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis"
VENV="$DATA/app"
EXTRAS=""
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --extras) EXTRAS="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '  %s\n' "$1"; }
step() { printf '\n> %s\n' "$1"; }

DESKTOP_FILE="$HOME/.local/share/applications/jarvis.desktop"
MAC_APP="$HOME/Applications/JARVIS.app"

if [ "$UNINSTALL" = 1 ]; then
  step "Entferne JARVIS"
  rm -rf "$VENV" "$DATA/window" "$DESKTOP_FILE" "$MAC_APP"
  say "Programm entfernt. Deine Daten liegen weiterhin in $DATA."
  exit 0
fi

[ -f "$SOURCE/pyproject.toml" ] || { echo "install.sh muss im JARVIS-Ordner liegen." >&2; exit 1; }

step "Suche Python 3.11 oder neuer"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    PYTHON=$(command -v "$candidate")
    break
  fi
done
if [ -z "$PYTHON" ]; then
  say "Kein passendes Python gefunden. Bitte Python 3.11+ installieren:"
  say "  macOS:  brew install python@3.12   oder https://www.python.org/downloads/"
  say "  Linux:  über die Paketverwaltung, z. B. sudo apt install python3 python3-venv"
  exit 1
fi
say "$PYTHON"

step "Richte die Programmumgebung ein"
mkdir -p "$DATA"
if [ ! -x "$VENV/bin/python" ]; then
  "$PYTHON" -m venv "$VENV" || { say "venv fehlt. Unter Debian/Ubuntu: sudo apt install python3-venv"; exit 1; }
fi
# keyring is required here: API keys go into the system keychain (Windows uses DPAPI).
TARGET="$SOURCE[keyring${EXTRAS:+,$EXTRAS}]"
"$VENV/bin/python" -m pip install --upgrade pip --quiet --disable-pip-version-check
"$VENV/bin/python" -m pip install --upgrade --quiet --disable-pip-version-check "$TARGET"
VERSION=$("$VENV/bin/python" -c 'import jarvis; print(jarvis.__version__)')
say "JARVIS $VERSION installiert"

step "Lege einen Starter an"
ICON=$("$VENV/bin/python" -c 'import jarvis, pathlib; print(pathlib.Path(jarvis.__file__).with_name("web") / "icon.png")')
mkdir -p "$HOME/JARVIS"
case "$(uname -s)" in
  Darwin)
    mkdir -p "$MAC_APP/Contents/MacOS"
    cat > "$MAC_APP/Contents/MacOS/JARVIS" <<EOF
#!/bin/sh
exec "$VENV/bin/jarvis-gui" "\$@"
EOF
    chmod +x "$MAC_APP/Contents/MacOS/JARVIS"
    cat > "$MAC_APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>JARVIS</string>
  <key>CFBundleExecutable</key><string>JARVIS</string>
  <key>CFBundleIdentifier</key><string>local.jarvis.assistant</string>
  <key>CFBundlePackageType</key><string>APPL</string>
</dict></plist>
EOF
    say "~/Applications/JARVIS.app"
    ;;
  *)
    mkdir -p "$(dirname "$DESKTOP_FILE")"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=JARVIS
Comment=Persönlicher Assistent, lokal auf deinem Rechner
Exec=$VENV/bin/jarvis-gui
Icon=$ICON
Terminal=false
Categories=Utility;
EOF
    say "Anwendungsmenü: JARVIS"
    ;;
esac

step "Prüfe Ollama (für lokale Modelle)"
if command -v ollama >/dev/null 2>&1 || [ -d /Applications/Ollama.app ]; then
  say "Ollama ist installiert."
else
  say "Ollama fehlt. Für ein lokales, kostenloses Modell: https://ollama.com/download"
fi

printf '\n  Fertig. Start über das Anwendungsmenü oder mit:\n    %s\n\n' "$VENV/bin/jarvis-gui"
