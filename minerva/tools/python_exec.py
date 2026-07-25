"""Python-Ausführung: kleine Berechnungen/Skripte in einem Subprozess.

Läuft in einem separaten Python-Prozess (Isolation gegen Absturz des Hauptprozesses),
mit dem venv-Interpreter. Für echte Systemaktionen ist run_shell/computer gedacht;
dieses Tool ist für Logik, Datenauswertung, Textverarbeitung.
"""
from __future__ import annotations

import subprocess
import sys

from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult

MAX_OUTPUT = 15_000


class PythonEvalTool(Tool):
    name = "python"
    description = (
        "Führt Python-Code in einem isolierten Subprozess aus und gibt stdout/stderr zurück. "
        "Ideal für Berechnungen, Datei-/Text-Verarbeitung, JSON, kleine Analysen. "
        "print() nutzen, um Ergebnisse sichtbar zu machen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python-Quelltext."},
            "timeout": {"type": "integer", "description": "Timeout in Sekunden (Default 60)."},
        },
        "required": ["code"],
    }

    def run(self, args, ctx: ToolContext):
        code = args.get("code", "")
        if not code.strip():
            return ToolResult(False, "Kein Code.")
        # DANGEROUS, nicht MODERATE: beliebiger Python-Code kann alles, was die
        # Shell kann (os.system, shutil.rmtree …). Als MODERATE lief er im
        # guarded-Modus ohne Rückfrage durch und umging damit die gesamte
        # Shell-Klassifikation des Guards. Siehe .bughunter/findings F3.
        decision = ctx.guard.review(
            "python", "Python-Ausführung", code[:400], Risk.DANGEROUS
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        timeout = int(args.get("timeout", 60))
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=str(ctx.workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Timeout nach {timeout}s.")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Fehler: {exc}")
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip()
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + "\n… [gekürzt]"
        return ToolResult(proc.returncode == 0, out or f"(kein Output, exit={proc.returncode})")
