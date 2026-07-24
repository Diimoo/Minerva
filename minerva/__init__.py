"""MINERVA — lokaler, sprachgesteuerter Desktop-Assistent.

Ein nativer Desktop-Agent (PyQt6), der:
  * per STT (faster-whisper) zuhört und per TTS (Higgs) antwortet,
  * ein umschaltbares Gehirn nutzt (Ollama lokal ODER Anthropic-API),
  * den Computer bedient (Shell, Dateien, Maus/Tastatur, Screenshots),
  * die CLI steuert und Claude Code aufrufen kann,
  * ein bestehendes RAG-Modul als Langzeitgedächtnis nutzt,
  * sich selbst erweitert, indem es neue Skills schreibt und heiß nachlädt.
"""
from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

# --- zentrale Pfade -------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = Path(__file__).resolve().parent

# Laufzeit-Verzeichnis (Config, Logs, generierte Skills, Audit, Gedächtnis).
MINERVA_HOME = Path(os.environ.get("MINERVA_HOME", Path.home() / ".minerva"))
CONFIG_PATH = MINERVA_HOME / "config.yaml"
LOG_DIR = MINERVA_HOME / "logs"
SKILLS_DIR = MINERVA_HOME / "skills"          # von MINERVA generierte Skills
MEMORY_DIR = MINERVA_HOME / "memory"
AUDIT_LOG = MINERVA_HOME / "audit.log"

# Externe Projekte, die MINERVA mitbenutzt (vom Nutzer bereitgestellt).
PROJ_DIR = Path(os.environ.get("MINERVA_PROJ_DIR", Path.home() / "Proj"))
RAG_MODULE_PATH = PROJ_DIR / "rag-module"
TTS_SERVER_DIR = PROJ_DIR / "TTS"


def ensure_dirs() -> None:
    """Legt alle Laufzeit-Verzeichnisse an (idempotent)."""
    for d in (MINERVA_HOME, LOG_DIR, SKILLS_DIR, MEMORY_DIR):
        d.mkdir(parents=True, exist_ok=True)
