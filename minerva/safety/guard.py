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

# --------------------------------------------------------------------------
# Strukturelle Sperren (F1). Die harte Sperrliste ist ein Substring-Vergleich
# und damit blind für Schreibvarianten ('rm -fr /' statt 'rm -rf /'). Weil im
# yolo-Modus NUR FORBIDDEN blockt, wird das Katastrophale hier anhand der
# geparsten Token erkannt, nicht anhand der Zeichenkette.
# --------------------------------------------------------------------------

# Befehle, die den Systemzustand ändern — unabhängig von der Sperrliste.
POWER_COMMANDS = {"shutdown", "reboot", "poweroff", "halt", "telinit"}
SYSTEMCTL_POWER_VERBS = {"poweroff", "reboot", "halt", "kexec", "emergency", "rescue"}

# Ziele, deren rekursives Löschen nie ohne harte Sperre passieren darf.
CATASTROPHIC_RM_TARGETS = {
    "/", "/*", "~", "~/", "$HOME", "$HOME/", "${HOME}",
    ".", "./", "..", "../", "*", "/.",
}

# Langformen von rm-Flags auf ihre Kurzform abgebildet.
_LONG_RM_FLAGS = {"--recursive": "r", "--force": "f", "--dir": "d"}

# Befehle aus READONLY_PREFIXES, die mit passenden Argumenten doch verändern (F2).
# Wert: (verändernde Argumente, Risiko bei Treffer)
READONLY_MUTATING_ARGS: dict[str, tuple[set[str], Risk]] = {
    "find": (
        {"-delete", "-exec", "-execdir", "-ok", "-okdir",
         "-fprint", "-fprintf", "-fls"},
        Risk.DANGEROUS,
    ),
    "ip": (
        {"set", "add", "del", "delete", "change", "replace", "flush"},
        Risk.MODERATE,
    ),
    "history": ({"-c", "-d", "-w", "-r", "-a", "-n"}, Risk.MODERATE),
}

# Verzeichnisse, aus denen ein Programm die SAFE-Einstufung erben darf (F4).
TRUSTED_BIN_DIRS = {"/bin", "/usr/bin", "/usr/local/bin", "/sbin", "/usr/sbin"}

# Verkettungsoperatoren — jedes Segment wird einzeln geprüft, damit
# 'cd / && rm -rf .' nicht am ersten harmlosen Befehl vorbeirutscht.
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")


def _normalize_command(command: str) -> str:
    """Kosmetische Varianz entfernen: Quotes weg, Whitespace kollabiert."""
    s = command.strip().lower().replace('"', "").replace("'", "")
    return re.sub(r"\s+", " ", s)


def _tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _strip_env_prefix(tokens: list[str]) -> list[str]:
    """Entfernt führende VAR=wert-Zuweisungen."""
    while tokens and "=" in tokens[0] and not tokens[0].startswith("-"):
        tokens = tokens[1:]
    return tokens


def _denylist_hit(command: str, banned: str) -> bool:
    """Prüft einen Sperrlisten-Eintrag am Befehlsanfang jedes Segments.

    Die Einträge benennen Befehle, keine Textschnipsel. Ein roher Substring-
    Vergleich sperrte deshalb auch 'rm -rf /tmp/scratch' (enthält 'rm -rf /')
    und 'cat shutdown-notes.txt' (enthält 'shutdown'). Siehe Fund F6.
    """
    wanted = _tokenize(_normalize_command(banned))
    if not wanted:
        return False
    for segment in _SEGMENT_SPLIT.split(_normalize_command(command)):
        tokens = _strip_env_prefix(_tokenize(segment))
        if tokens[: len(wanted)] == wanted:
            return True
    return False


# Immer katastrophal, unabhängig von jeder Sperrliste.
PARTITION_COMMANDS = {"fdisk", "sfdisk", "cfdisk", "gdisk", "parted", "wipefs"}
_BLOCK_DEVICE = r"/dev/(?:sd[a-z]|nvme\d+n\d+|hd[a-z]|vd[a-z]|mmcblk\d+)"
_BLOCK_DEVICE_RE = re.compile(_BLOCK_DEVICE)
_REDIRECT_TO_DEVICE_RE = re.compile(r">\s*" + _BLOCK_DEVICE)
_FORK_BOMB_RE = re.compile(r":\s*\(\s*\)\s*\{")


def _rm_flags_and_targets(tokens: list[str]) -> tuple[set[str], list[str]]:
    """Trennt rm-Flags (Kurz-, Lang-, Einzelform) von den Zielen."""
    flags: set[str] = set()
    targets: list[str] = []
    for t in tokens[1:]:
        low = t.lower()
        if low in _LONG_RM_FLAGS:
            flags.add(_LONG_RM_FLAGS[low])
        elif low.startswith("--"):
            continue
        elif low.startswith("-") and len(low) > 1:
            flags.update(low[1:])
        else:
            targets.append(t)
    return flags, targets


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

    @staticmethod
    def catastrophic_reason(command: str) -> Optional[str]:
        """Strukturelle Sperre (F1): erkennt Wirkung, nicht Schreibweise.

        Arbeitet auf geparsten Token je Verkettungssegment. Damit greifen
        'rm -fr /', 'rm -r -f /', 'rm --recursive --force /', "rm -rf '/'"
        und 'cd / && rm -rf .' gleichermaßen — anders als der Substring-
        Vergleich der Sperrliste.
        """
        # Zuerst auf dem ungeteilten Befehl prüfen: die Fork-Bombe enthält
        # selbst '|' und ';' und würde von der Segmentteilung zerlegt.
        if _FORK_BOMB_RE.search(command):
            return "Fork-Bombe"
        if _REDIRECT_TO_DEVICE_RE.search(command):
            return "Schreibt direkt auf ein Blockgerät"

        for segment in _SEGMENT_SPLIT.split(command):
            tokens = _strip_env_prefix(_tokenize(segment))
            if not tokens:
                continue
            cmd = Path(tokens[0]).name.lower()

            if cmd.startswith("mkfs"):
                return f"Formatiert ein Dateisystem ({cmd})"
            if cmd in PARTITION_COMMANDS:
                return f"Partitionierung/Löschung ({cmd})"
            if cmd == "dd" and any(
                t.lower().startswith("of=") and _BLOCK_DEVICE_RE.search(t.lower())
                for t in tokens[1:]
            ):
                return "Schreibt mit dd auf ein Blockgerät"

            if cmd in POWER_COMMANDS:
                return f"Ändert den Systemzustand ({cmd})"
            if cmd == "systemctl" and any(
                a.lower() in SYSTEMCTL_POWER_VERBS for a in tokens[1:]
            ):
                return "Ändert den Systemzustand (systemctl)"

            if cmd == "rm":
                flags, targets = _rm_flags_and_targets(tokens)
                if "--no-preserve-root" in (t.lower() for t in tokens):
                    return "Rekursives Löschen ohne Root-Schutz"
                if flags & {"r"} and "f" in flags:
                    for t in targets:
                        if t in CATASTROPHIC_RM_TARGETS or t.rstrip("/") in ("", "~", "$HOME"):
                            return f"Rekursives Löschen von {t!r}"
        return None

    def classify_shell(self, command: str) -> tuple[Risk, str]:
        # 1. Strukturelle Sperre — schreibweisenunabhängig, gilt auch in yolo.
        reason = self.catastrophic_reason(command)
        if reason:
            return Risk.FORBIDDEN, reason

        # 2. Nutzerdefinierte Sperrliste, gegen normalisierte Form verglichen,
        #    damit doppelte Leerzeichen und Quotes sie nicht aushebeln.
        normalized = _normalize_command(command)
        for banned in self.hard_denylist:
            if banned and _denylist_hit(command, banned):
                return Risk.FORBIDDEN, f"Steht auf der harten Sperrliste: {banned!r}"

        # 3. Gefährliche Muster.
        for pat, why in DANGEROUS_PATTERNS:
            if pat.search(normalized):
                return Risk.DANGEROUS, why

        # 4. Erstes echtes Token bestimmen (nach VAR=wert-Präfixen).
        tokens = _tokenize(command)
        raw = ""
        for t in tokens:
            if "=" in t and not t.startswith("-"):
                continue  # VAR=wert prefix
            raw = t
            break
        first = Path(raw).name if raw else ""

        # 5. Verändernde Argumente heben die SAFE-Einstufung auf (F2).
        mutating = READONLY_MUTATING_ARGS.get(first)
        if mutating:
            args = {a.lower() for a in tokens[1:]}
            if args & mutating[0]:
                return mutating[1], f"{first} mit veränderndem Argument"

        # 6. SAFE nur für vertrauenswürdige Herkunft (F4): bloßer Name oder
        #    ein Systemverzeichnis. '/tmp/evil/ls' erbt nichts von 'ls'.
        if "/" in raw:
            trusted = str(Path(raw).parent) in TRUSTED_BIN_DIRS
        else:
            trusted = True

        if trusted and first in READONLY_PREFIXES and not re.search(r"[>|]", command):
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
