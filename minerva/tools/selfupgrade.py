"""Selbst-Upgrade: Minerva verbessert ihren EIGENEN Code — sicher.

Ablauf (nach Wunsch des Nutzers):
  1. Kopie des Projekts in einen Arbeitsordner anlegen.
  2. Claude Code die Verbesserung in der Kopie umsetzen lassen.
  3. Validieren: `python -m minerva --selftest` in der Kopie muss bestehen.
  4. Aktuelles Projekt sichern (Backup) und die Kopie übernehmen.
  5. Erneut validieren; bei Fehler automatisch aus dem Backup zurückrollen.
  6. Optional: Minerva startet sich selbst neu.

Sicherheitsnetze: Backup + automatischer Rollback, Validierungs-Gate vor UND
nach der Übernahme, Ausschluss von .venv/.git/Laufzeitdaten. Läuft durch den
Guard (im yolo-Modus ohne Rückfrage, aber protokolliert).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .. import MINERVA_HOME, PROJECT_ROOT
from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult

log = logging.getLogger("minerva.tools.selfupgrade")

EXCLUDES = [".venv", ".git", "__pycache__", "screenshots", "logs", "*.pyc", ".mypy_cache"]


def _rollback_failed(backup: Path, why: str) -> str:
    """Meldung für den schlimmsten Fall: defekt UND Rollback gescheitert."""
    return (
        f"{why}\n\n"
        f"ACHTUNG: Der automatische Rollback ist EBENFALLS fehlgeschlagen. "
        f"Der Projektordner kann in einem inkonsistenten Zustand sein. "
        f"Das Backup liegt unter {backup} — bitte von Hand zurückspielen, z. B.:\n"
        f"  rsync -a --delete --exclude .venv --exclude .git {backup}/ {PROJECT_ROOT}/"
    )


def _copy_tree(src: Path, dst: Path) -> None:
    if shutil.which("rsync"):
        args = ["rsync", "-a", "--delete"]
        for e in EXCLUDES:
            args += ["--exclude", e]
        args += [f"{src}/", f"{dst}/"]
        subprocess.run(args, check=True)
    else:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*EXCLUDES))


def _selftest(project_dir: Path) -> tuple[bool, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(project_dir)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "minerva", "--selftest"],
            cwd=str(project_dir), capture_output=True, text=True, timeout=180, env=env,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"Selbsttest-Ausnahme: {exc}"
    out = (proc.stdout + "\n" + proc.stderr).strip()
    return proc.returncode == 0, out[-2000:]


class SelfUpgradeTool(Tool):
    name = "self_upgrade"
    description = (
        "Verbessert Minervas EIGENEN Quellcode sicher und autonom: legt eine Kopie des Projekts "
        "an, lässt Claude Code die gewünschte Verbesserung dort umsetzen, prüft mit einem "
        "Selbsttest, dass alles noch funktioniert, sichert den aktuellen Stand, übernimmt die "
        "Verbesserung und startet Minerva optional neu. Bei Fehlern wird automatisch "
        "zurückgerollt. Nutze dies, wenn du dich selbst erweitern oder deinen Code verbessern sollst."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Was genau am Code verbessert/ergänzt werden soll."},
            "restart": {"type": "boolean", "description": "Nach erfolgreicher Übernahme neu starten (Default true)."},
            "timeout": {"type": "integer", "description": "Zeitlimit für Claude Code in Sekunden (Default 1800)."},
        },
        "required": ["task"],
    }

    def run(self, args, ctx: ToolContext):
        task = (args.get("task") or "").strip()
        if not task:
            return ToolResult(False, "Keine Aufgabe angegeben.")
        restart = bool(args.get("restart", True))
        timeout = int(args.get("timeout", 1800))

        claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
        if not os.path.exists(claude_bin):
            return ToolResult(False, "`claude` CLI nicht gefunden.")

        decision = ctx.guard.review(
            "self_upgrade", "Selbst-Upgrade des eigenen Codes",
            f"{task[:300]}\n(Kopie → Claude Code → Test → Übernahme{' → Neustart' if restart else ''})",
            Risk.DANGEROUS,
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")

        ts = time.strftime("%Y%m%d-%H%M%S")
        work = MINERVA_HOME / "upgrades" / ts
        backup = MINERVA_HOME / "backups" / ts
        work.parent.mkdir(parents=True, exist_ok=True)
        backup.parent.mkdir(parents=True, exist_ok=True)

        # 1) Kopie anlegen
        ctx.emit("info", f"Selbst-Upgrade: erstelle Arbeitskopie in {work} …")
        try:
            _copy_tree(PROJECT_ROOT, work)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Kopie fehlgeschlagen: {exc}")

        # 2) Claude Code die Verbesserung umsetzen lassen
        prompt = (
            "Du arbeitest an 'Minerva', einem lokalen Sprach-Assistenten (Python-Paket 'minerva'). "
            "Setze folgende Verbesserung am Code um und halte den Assistenten dabei voll funktionsfähig. "
            "WICHTIG: Nach deiner Änderung MUSS der Befehl "
            f"`PYTHONPATH={work} {sys.executable} -m minerva --selftest` mit Exit-Code 0 durchlaufen — "
            "führe ihn selbst aus und behebe alle Fehler, bis er sauber ist. Ändere keine Dateien "
            "außerhalb dieses Projektordners, und fasse .venv/.git nicht an.\n\n"
            f"Aufgabe:\n{task}"
        )
        ctx.emit("info", "Selbst-Upgrade: Claude Code arbeitet an der Kopie …")
        try:
            proc = subprocess.run(
                [claude_bin, "-p", prompt, "--dangerously-skip-permissions"],
                cwd=str(work), capture_output=True, text=True, timeout=timeout, env=dict(os.environ),
            )
            claude_out = (proc.stdout or "")[-1500:]
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Claude Code Timeout nach {timeout}s. Kopie belassen unter {work}.")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Claude-Code-Fehler: {exc}")

        # 3) Validieren (Kopie)
        ctx.emit("info", "Selbst-Upgrade: validiere die Kopie …")
        ok, test_out = _selftest(work)
        if not ok:
            return ToolResult(False, f"Validierung der Kopie fehlgeschlagen — Übernahme abgebrochen.\n{test_out}")

        # 4) Backup des aktuellen Stands
        ctx.emit("info", f"Selbst-Upgrade: sichere aktuellen Stand nach {backup} …")
        try:
            _copy_tree(PROJECT_ROOT, backup)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Backup fehlgeschlagen — Übernahme abgebrochen: {exc}")

        # 5) Übernahme (Kopie -> Projekt), dann erneut validieren
        ctx.emit("info", "Selbst-Upgrade: übernehme die Verbesserung …")
        try:
            _sync_into(work, PROJECT_ROOT)
        except Exception as exc:  # noqa: BLE001
            if _restore(backup, PROJECT_ROOT):
                return ToolResult(False, f"Übernahme fehlgeschlagen, zurückgerollt: {exc}")
            return ToolResult(False, _rollback_failed(backup, f"Übernahme fehlgeschlagen: {exc}"))

        ok2, test_out2 = _selftest(PROJECT_ROOT)
        if not ok2:
            ctx.emit("warn", "Selbst-Upgrade: Validierung nach Übernahme fehlgeschlagen — Rollback.")
            if _restore(backup, PROJECT_ROOT):
                return ToolResult(False, f"Nach Übernahme defekt — automatisch zurückgerollt.\n{test_out2}")
            ctx.emit("error", "Selbst-Upgrade: ROLLBACK FEHLGESCHLAGEN — Handarbeit nötig!")
            return ToolResult(False, _rollback_failed(backup, f"Nach Übernahme defekt.\n{test_out2}"))

        # 6) Erfolg — optional Neustart
        msg = (f"Selbst-Upgrade erfolgreich übernommen (Backup: {backup}). "
               f"Claude-Code-Notiz: {claude_out[-300:]}")
        if restart and getattr(ctx, "app", None) is not None:
            ctx.emit("info", "Selbst-Upgrade abgeschlossen — Minerva startet neu …")
            try:
                ctx.app.schedule_restart(2.0)
                return ToolResult(True, msg + " Neustart wird ausgelöst.")
            except Exception as exc:  # noqa: BLE001
                return ToolResult(True, msg + f" (Neustart fehlgeschlagen: {exc}; bitte manuell neu starten.)")
        return ToolResult(True, msg + (" Bitte manuell neu starten." if restart else ""))


def _sync_into(src: Path, dst: Path) -> None:
    """Übernimmt src -> dst (nur Code/Projektdateien, ohne Laufzeit/venv)."""
    if shutil.which("rsync"):
        args = ["rsync", "-a", "--delete"]
        for e in EXCLUDES:
            args += ["--exclude", e]
        args += [f"{src}/", f"{dst}/"]
        subprocess.run(args, check=True)
    else:
        for item in src.iterdir():
            if item.name in EXCLUDES:
                continue
            target = dst / item.name
            if item.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(item, target, ignore=shutil.ignore_patterns(*EXCLUDES))
            else:
                shutil.copy2(item, target)


def _restore(backup: Path, dst: Path) -> bool:
    """Rollt aus dem Backup zurück. Gibt zurück, ob es geklappt hat.

    Vorher wurde jede Ausnahme geschluckt und der Aufrufer meldete dem Nutzer
    trotzdem „automatisch zurückgerollt" — auf dem sicherheitskritischsten Pfad
    des Projekts also möglicherweise eine Lüge. Siehe Fund F10.

    Wirft weiterhin nicht: der Aufrufer steckt schon im Fehlerfall, und eine
    zweite Ausnahme würde die Meldung ganz verhindern. Er muss den Rückgabewert
    auswerten.
    """
    try:
        _sync_into(backup, dst)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("Rollback aus %s nach %s fehlgeschlagen: %s", backup, dst, exc)
        return False
