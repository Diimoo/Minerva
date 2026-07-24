"""Sicherheits-Gatekeeper für potenziell gefährliche Aktionen.

Jede riskante Aktion (Shell, Datei-Schreiben, Computer-Steuerung, Selbst-
modifikation) läuft durch `Guard.review(...)`. Die Entscheidung hängt vom
Sicherheitsmodus ab:

  * "readonly" — nur eindeutig lesende/ungefährliche Aktionen werden erlaubt.
  * "guarded"  — gefährliche Aktionen brauchen eine Bestätigung (GUI/Voice).
  * "yolo"     — alles außer der harten Sperrliste läuft ohne Rückfrage.

Die Bestätigung selbst wird über einen Callback (`confirm_fn`) eingeholt, den
die App an das GUI koppelt. Ohne Callback verweigert "guarded" gefährliche
Aktionen (fail-safe).
"""
from __future__ import annotations

import logging
import re
import shlex
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from .. import AUDIT_LOG

log = logging.getLogger("minerva.safety")

# Callback: (titel, detail, risiko) -> True (erlaubt) / False (abgelehnt)
ConfirmFn = Callable[[str, str, str], bool]


class Risk(str, Enum):
    SAFE = "safe"
    MODERATE = "moderate"
    DANGEROUS = "dangerous"
    FORBIDDEN = "forbidden"


@dataclass
class GuardDecision:
    allowed: bool
    risk: Risk
    reason: str


# Kommandos, die (mit erstem Token) als eindeutig lesend gelten.
# Bewusst KONSERVATIV: alles, was schreiben/laden kann (python, pip, git, curl,
# node, npm …), gilt als 'moderate' — im readonly-Modus damit blockiert, im
# guarded-Modus ohne Rückfrage erlaubt.
READONLY_PREFIXES = {
    "ls", "cat", "pwd", "echo", "whoami", "id", "date", "uptime", "df", "du",
    "free", "uname", "hostname", "env", "printenv", "which", "type", "file",
    "head", "tail", "wc", "grep", "rg", "find", "stat", "tree", "ps",
    "nvidia-smi", "ss", "netstat", "ip", "history", "man",
    "less", "more", "sort", "uniq", "cut", "jq", "diff", "md5sum", "sha256sum",
}

# Muster, die immer eine Bestätigung/Blockade auslösen (gefährlich).
DANGEROUS_PATTERNS = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*"), "Löscht Dateien/Verzeichnisse"),
    (re.compile(r"\bmv\b"), "Verschiebt/überschreibt Dateien"),
    (re.compile(r"\bdd\b"), "Direkter Datenträger-Zugriff (dd)"),
    (re.compile(r"\bmkfs"), "Formatiert Dateisystem"),
    (re.compile(r"\bchmod\b|\bchown\b"), "Ändert Rechte/Eigentümer"),
    (re.compile(r"\bsudo\b|\bsu\b"), "Erhöhte Rechte (sudo/su)"),
    (re.compile(r"\bkill(all)?\b|\bpkill\b"), "Beendet Prozesse"),
    (re.compile(r"\bsystemctl\b|\bservice\b"), "Steuert Systemdienste"),
    (re.compile(r"\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b"), "Fährt System herunter/neu"),
    (re.compile(r"\bmkfs|\bfdisk|\bparted|\bwipefs"), "Partitionierung/Formatierung"),
    (re.compile(r">\s*/dev/"), "Schreibt auf Geräteknoten"),
    (re.compile(r"\bcurl\b.*\|\s*(sudo\s+)?(ba)?sh\b|\bwget\b.*\|\s*(ba)?sh\b"), "Pipe aus dem Netz in die Shell"),
    (re.compile(r"\bgit\s+push\b.*(--force|-f)\b"), "Force-Push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "Verwirft lokale Änderungen"),
    (re.compile(r"\bnpm\s+publish\b|\bpip\s+.*upload\b|\btwine\b"), "Veröffentlicht Pakete"),
    (re.compile(r":\(\)\s*\{"), "Fork-Bombe"),
    (re.compile(r"\bcrontab\b"), "Ändert geplante Aufgaben"),
]


class Guard:
    def __init__(
        self,
        mode: str = "guarded",
        hard_denylist: Optional[list[str]] = None,
        confirm_timeout_s: int = 60,
        audit: bool = True,
    ) -> None:
        self.mode = mode
        self.hard_denylist = [d.lower() for d in (hard_denylist or [])]
        self.confirm_timeout_s = confirm_timeout_s
        self.audit_enabled = audit
        self.confirm_fn: Optional[ConfirmFn] = None

    # -- öffentliche API ---------------------------------------------------
    def set_confirm_fn(self, fn: ConfirmFn) -> None:
        self.confirm_fn = fn

    def classify_shell(self, command: str) -> tuple[Risk, str]:
        low = command.strip().lower()
        for banned in self.hard_denylist:
            if banned and banned in low:
                return Risk.FORBIDDEN, f"Steht auf der harten Sperrliste: {banned!r}"
        for pat, why in DANGEROUS_PATTERNS:
            if pat.search(low):
                return Risk.DANGEROUS, why
        # Erstes Token bestimmen (nach Umgebungszuweisungen/Klammern).
        try:
            tokens = shlex.split(command)
        except ValueError:
            tokens = command.split()
        first = ""
        for t in tokens:
            if "=" in t and not t.startswith("-"):
                continue  # VAR=wert prefix
            first = Path(t).name
            break
        if first in READONLY_PREFIXES and not re.search(r"[>|]", command):
            return Risk.SAFE, "Lesendes Kommando"
        return Risk.MODERATE, "Verändernde/unbekannte Aktion"

    def review(self, kind: str, title: str, detail: str, risk: Risk) -> GuardDecision:
        """Zentrale Freigabe-Logik für eine Aktion."""
        # Harte Sperre gilt immer.
        if risk == Risk.FORBIDDEN:
            self._audit(kind, title, detail, "FORBIDDEN")
            return GuardDecision(False, risk, "Aktion ist grundsätzlich gesperrt.")

        if self.mode == "yolo":
            self._audit(kind, title, detail, "ALLOW(yolo)")
            return GuardDecision(True, risk, "yolo-Modus")

        if self.mode == "readonly":
            if risk == Risk.SAFE:
                self._audit(kind, title, detail, "ALLOW(readonly-safe)")
                return GuardDecision(True, risk, "Lesende Aktion im readonly-Modus")
            self._audit(kind, title, detail, "DENY(readonly)")
            return GuardDecision(False, risk, "readonly-Modus: nur lesende Aktionen erlaubt.")

        # guarded
        if risk == Risk.SAFE:
            self._audit(kind, title, detail, "ALLOW(safe)")
            return GuardDecision(True, risk, "Ungefährlich")
        if risk == Risk.MODERATE:
            # Moderate Aktionen werden erlaubt, aber protokolliert.
            self._audit(kind, title, detail, "ALLOW(moderate)")
            return GuardDecision(True, risk, "Erlaubt (protokolliert)")

        # DANGEROUS -> Bestätigung einholen
        approved = self._ask_confirm(title, detail, risk.value)
        self._audit(kind, title, detail, "ALLOW(confirmed)" if approved else "DENY(declined)")
        return GuardDecision(
            approved,
            risk,
            "Vom Nutzer bestätigt" if approved else "Vom Nutzer abgelehnt/Timeout.",
        )

    # -- intern ------------------------------------------------------------
    def _ask_confirm(self, title: str, detail: str, risk: str) -> bool:
        if self.confirm_fn is None:
            log.warning("Kein Bestätigungs-Callback gesetzt -> gefährliche Aktion abgelehnt.")
            return False
        try:
            return bool(self.confirm_fn(title, detail, risk))
        except Exception as exc:  # noqa: BLE001
            log.error("Bestätigung fehlgeschlagen: %s", exc)
            return False

    def _audit(self, kind: str, title: str, detail: str, verdict: str) -> None:
        if not self.audit_enabled:
            return
        try:
            ts = datetime.now(timezone.utc).isoformat()
            line = f"{ts}\t{verdict}\t{kind}\t{title}\t{detail}".replace("\n", " ⏎ ")
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
