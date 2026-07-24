#!/usr/bin/env bash
# Installiert/entfernt Minerva als GNOME-Autostart (startet beim Login).
# Minerva bleibt dabei ressourcenschonend: STT/LLM/TTS laden erst bei Bedarf,
# im Leerlauf läuft nur die günstige Weckwort-Erkennung (VAD).
#
#   ./scripts/install-autostart.sh          # aktivieren
#   ./scripts/install-autostart.sh --remove # deaktivieren
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/.config/autostart/minerva.desktop"

if [ "${1:-}" = "--remove" ]; then
    rm -f "$DEST" && echo "Autostart entfernt: $DEST"
    exit 0
fi

mkdir -p "$HOME/.config/autostart"
# Exec-Pfad auf den tatsächlichen Projektpfad setzen.
sed "s|^Exec=.*|Exec=${HERE}/run.sh|" "${HERE}/scripts/minerva.desktop" > "$DEST"
chmod +x "${HERE}/run.sh"
echo "Autostart aktiviert: $DEST"
echo "Minerva startet ab dem nächsten Login automatisch."
