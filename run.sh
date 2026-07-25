#!/usr/bin/env bash
# MINERVA-Launcher. Aktiviert das venv und startet die App.
#
#   ./run.sh              # native GUI
#   ./run.sh --cli        # Text-REPL im Terminal
#   ./run.sh --no-voice   # GUI ohne Sprache
#   ./run.sh --backend anthropic   # Anthropic-API statt Ollama (ANTHROPIC_API_KEY nötig)
#   ./run.sh --backend claude_code # Claude über das Pro/Max-Abo (kein API-Key nötig)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if [ -d ".venv" ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# XWayland/xcb sorgt für zuverlässiges Overlay + Fensterpositionierung.
if [ -z "${QT_QPA_PLATFORM:-}" ] && [ -n "${DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM=xcb
fi

exec python -m minerva "$@"
