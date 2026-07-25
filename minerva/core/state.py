"""Zustände von MINERVA (steuern u. a. die Orb-Animation)."""
from __future__ import annotations

from enum import Enum


class AgentState(str, Enum):
    IDLE = "idle"              # wartet
    LISTENING = "listening"    # Mikrofon aktiv, wartet auf Sprache
    HEARING = "hearing"        # nimmt gerade Sprache auf
    LOADING = "loading"        # STT-Modell wird geladen (erster Lauf)
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"      # LLM denkt / nutzt Werkzeuge
    SPEAKING = "speaking"      # TTS spricht
    ERROR = "error"
