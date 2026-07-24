"""Claude-Code-Werkzeug: delegiert komplexe Coding-Aufgaben an die `claude` CLI.

MINERVA ruft Claude Code headless (`claude -p`) in einem Zielverzeichnis auf. So
kann der Assistent große, mehrstufige Programmieraufgaben an ein spezialisiertes
Agenten-System auslagern, statt sie selbst Zeile für Zeile zu erledigen.
"""
from __future__ import annotations

import os
import shutil
import subprocess

from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult

MAX_OUTPUT = 24_000


class ClaudeCodeTool(Tool):
    name = "claude_code"
    description = (
        "Beauftragt Claude Code (die `claude` CLI) headless mit einer Programmier-/"
        "Automatisierungsaufgabe in einem Verzeichnis. Nutze dies für umfangreiche Coding-"
        "Aufgaben: Feature bauen, Bug fixen, Refactoring, Tests schreiben, Repos analysieren. "
        "Gib eine klare, vollständige Aufgabenbeschreibung. Läuft autonom bis zu 'timeout' Sekunden."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Vollständige Aufgabenbeschreibung für Claude Code."},
            "cwd": {"type": "string", "description": "Projektverzeichnis (Default: aktuelles Arbeitsverzeichnis)."},
            "autonomous": {
                "type": "boolean",
                "description": "true = ohne Rückfragen ausführen (--dangerously-skip-permissions). Erfordert Bestätigung.",
            },
            "timeout": {"type": "integer", "description": "Timeout in Sekunden (Default 900)."},
        },
        "required": ["task"],
    }

    def run(self, args, ctx: ToolContext):
        claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
        if not os.path.exists(claude_bin):
            return ToolResult(False, "`claude` CLI nicht gefunden.")

        task = args.get("task", "").strip()
        if not task:
            return ToolResult(False, "Leere Aufgabe.")
        if args.get("cwd"):
            ctx.set_workdir(args["cwd"])
        cwd = str(ctx.workdir)
        timeout = int(args.get("timeout", 900))
        autonomous = bool(args.get("autonomous", False))

        risk = Risk.DANGEROUS if autonomous else Risk.MODERATE
        decision = ctx.guard.review(
            "claude_code",
            "Claude Code beauftragen" + (" (autonom)" if autonomous else ""),
            f"cwd={cwd}\n{task[:400]}",
            risk,
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")

        cmd = [claude_bin, "-p", task]
        if autonomous:
            cmd.append("--dangerously-skip-permissions")

        ctx.emit("info", f"Claude Code arbeitet in {cwd} …")
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=dict(os.environ),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Claude Code Timeout nach {timeout}s.")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Fehler beim Aufruf: {exc}")

        out = (proc.stdout or "").strip()
        if proc.stderr:
            out += "\n[stderr]\n" + proc.stderr.strip()
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + "\n… [gekürzt]"
        return ToolResult(proc.returncode == 0, out or f"(kein Output, exit={proc.returncode})")
