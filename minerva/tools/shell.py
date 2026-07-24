"""Shell-Werkzeug: führt CLI-Befehle im Arbeitsverzeichnis aus.

Damit bedient MINERVA die Kommandozeile. Jeder Befehl läuft durch den Guard;
gefährliche Kommandos erfordern eine Bestätigung.
"""
from __future__ import annotations

import os
import subprocess

from .registry import Tool, ToolContext, ToolResult

MAX_OUTPUT = 20_000  # Zeichen


class ShellTool(Tool):
    name = "run_shell"
    description = (
        "Führt einen Shell-Befehl (bash) im aktuellen Arbeitsverzeichnis aus und gibt "
        "stdout+stderr sowie den Exit-Code zurück. Für Verzeichniswechsel 'cwd' setzen; "
        "das ändert das dauerhafte Arbeitsverzeichnis von MINERVA. Nutze dies, um Programme "
        "zu starten, git zu bedienen, Dateien zu bearbeiten, Builds/Tests laufen zu lassen usw."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Der auszuführende Befehl (bash -c)."},
            "cwd": {"type": "string", "description": "Optional: Arbeitsverzeichnis für diesen Befehl (wird dauerhaft übernommen)."},
            "timeout": {"type": "integer", "description": "Timeout in Sekunden (Default 120)."},
        },
        "required": ["command"],
    }

    def run(self, args, ctx: ToolContext):
        command = (args.get("command") or "").strip()
        if not command:
            return ToolResult(False, "Leerer Befehl.")

        if args.get("cwd"):
            ctx.set_workdir(args["cwd"])
        cwd = str(ctx.workdir)
        timeout = int(args.get("timeout", 120))

        risk, why = ctx.guard.classify_shell(command)
        decision = ctx.guard.review("shell", f"Shell: {command[:80]}", f"cwd={cwd}\n{command}\n({why})", risk)
        if not decision.allowed:
            return ToolResult(False, f"Befehl abgelehnt ({decision.risk.value}): {decision.reason}")

        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Timeout nach {timeout}s: {command}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Ausführungsfehler: {exc}")

        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip()
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + f"\n… [Ausgabe gekürzt, {len(out)} Zeichen]"
        header = f"exit={proc.returncode} cwd={cwd}"
        return ToolResult(proc.returncode == 0, f"{header}\n{out}" if out else header)
