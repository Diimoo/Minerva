"""Selbstverbesserung: MINERVA baut sich neue Fähigkeiten (Skills).

Zwei Wege:
  1. create_skill  — MINERVA schreibt selbst den Python-Code eines neuen Skills.
  2. build_skill_with_claude_code — delegiert das Bauen an Claude Code (für
     komplexe Fähigkeiten), inklusive Vorlage und Reload.

Jeder neue Skill ist ausführbarer Code, der IM PROZESS von MINERVA läuft — daher
läuft die Erstellung durch den Guard (Bestätigung) und eine Syntaxprüfung.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

from .. import SKILLS_DIR
from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def _syntax_ok(code: str) -> tuple[bool, str]:
    try:
        compile(code, "<skill>", "exec")
        return True, "ok"
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc}"


class CreateSkillTool(Tool):
    name = "create_skill"
    description = (
        "Erstellt einen neuen Skill (neue Fähigkeit) für MINERVA aus vollständigem Python-Code "
        "und lädt ihn sofort. Der Code MUSS eine Funktion get_tools() definieren, die eine Liste "
        "von Tool-Instanzen zurückgibt (siehe Vorlage via list_skills). Nutze dies, um dir selbst "
        "dauerhaft neue Werkzeuge beizubringen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Dateiname ohne Endung, klein/snake_case (z. B. 'weather')."},
            "code": {"type": "string", "description": "Vollständiger Python-Quelltext des Skills."},
            "overwrite": {"type": "boolean", "description": "Vorhandenen Skill überschreiben."},
        },
        "required": ["name", "code"],
    }

    def run(self, args, ctx: ToolContext):
        name = (args.get("name") or "").strip().lower()
        if not _SAFE_NAME.match(name):
            return ToolResult(False, "Ungültiger Name (nur a-z, 0-9, _; Beginn mit Buchstabe).")
        code = args.get("code", "")
        ok, msg = _syntax_ok(code)
        if not ok:
            return ToolResult(False, f"Code hat einen Syntaxfehler: {msg}")

        path = SKILLS_DIR / f"{name}.py"
        if path.exists() and not args.get("overwrite"):
            return ToolResult(False, f"Skill {name} existiert bereits (overwrite=true zum Ersetzen).")

        decision = ctx.guard.review(
            "self_improve",
            f"Neuen Skill erstellen: {name}",
            f"{len(code)} Zeichen Python, läuft im MINERVA-Prozess.\n{code[:400]}",
            Risk.DANGEROUS,
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")

        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")

        if ctx.skill_manager is None or ctx.registry is None:
            return ToolResult(True, f"Skill {name} geschrieben, aber kein Loader angebunden (Neustart nötig).")
        count, load_msg = ctx.skill_manager.load_file(path, ctx.registry)
        if count == 0:
            return ToolResult(False, f"Skill geschrieben, aber Laden fehlgeschlagen: {load_msg}")
        ctx.emit("info", f"Neuer Skill aktiv: {name} ({count} Tool(s))")
        return ToolResult(True, f"Skill {name} erstellt und geladen. {load_msg}")


class ListSkillsTool(Tool):
    name = "list_skills"
    description = (
        "Listet vorhandene Skills und zeigt die Skill-Vorlage. Nutze dies vor create_skill, "
        "um das erwartete Format zu kennen."
    )
    parameters = {"type": "object", "properties": {}}

    def run(self, args, ctx: ToolContext):
        from ..skills import SKILL_TEMPLATE

        skills = ctx.skill_manager.list_skills() if ctx.skill_manager else []
        listing = "\n".join(f"  - {p.name}" for p in skills) or "  (noch keine)"
        example = SKILL_TEMPLATE.format(
            title="Beispiel",
            description="Kurzbeschreibung der Fähigkeit.",
            classname="ExampleTool",
            tool_name="example_action",
            tool_desc="Was dieses Tool tut.",
            parameters='{"type": "object", "properties": {"arg": {"type": "string"}}, "required": ["arg"]}',
        )
        return ToolResult(True, f"Vorhandene Skills:\n{listing}\n\nVorlage:\n{example}")


class ReloadSkillsTool(Tool):
    name = "reload_skills"
    description = "Lädt alle Skills neu (nach manuellen Änderungen an den Skill-Dateien)."
    parameters = {"type": "object", "properties": {}}

    def run(self, args, ctx: ToolContext):
        if ctx.skill_manager is None or ctx.registry is None:
            return ToolResult(False, "Kein Skill-Loader angebunden.")
        msgs = ctx.skill_manager.reload(ctx.registry)
        return ToolResult(True, "Skills neu geladen:\n" + "\n".join(f"  {m}" for m in msgs))


class DelegateToClaudeCodeTool(Tool):
    name = "build_skill_with_claude_code"
    description = (
        "Lässt Claude Code einen neuen, komplexen Skill für MINERVA bauen (Datei in den "
        "Skills-Ordner schreiben) und lädt ihn danach automatisch. Nutze dies für anspruchsvolle "
        "Fähigkeiten, die viel Code oder externe Bibliotheken brauchen. Beschreibe die gewünschte "
        "Fähigkeit möglichst genau."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill-Name (snake_case)."},
            "specification": {"type": "string", "description": "Genaue Beschreibung der gewünschten Fähigkeit."},
            "timeout": {"type": "integer", "description": "Timeout in Sekunden (Default 900)."},
        },
        "required": ["name", "specification"],
    }

    def run(self, args, ctx: ToolContext):
        name = (args.get("name") or "").strip().lower()
        if not _SAFE_NAME.match(name):
            return ToolResult(False, "Ungültiger Skill-Name.")
        spec = args.get("specification", "").strip()
        if not spec:
            return ToolResult(False, "Keine Spezifikation angegeben.")

        claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
        if not os.path.exists(claude_bin):
            return ToolResult(False, "`claude` CLI nicht gefunden.")

        decision = ctx.guard.review(
            "self_improve",
            f"Claude Code baut Skill: {name}",
            spec[:400],
            Risk.DANGEROUS,
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")

        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        target = SKILLS_DIR / f"{name}.py"
        from ..skills import SKILL_TEMPLATE

        template = SKILL_TEMPLATE.format(
            title=name,
            description=spec[:200],
            classname="".join(p.capitalize() for p in name.split("_")) + "Tool",
            tool_name=name,
            tool_desc=spec[:120],
            parameters='{"type": "object", "properties": {}}',
        )
        prompt = (
            "Du baust einen Skill für den Assistenten MINERVA. Ein Skill ist eine einzelne "
            f"Python-Datei unter {target}. Sie MUSS eine Funktion get_tools() definieren, die "
            "eine Liste von Tool-Instanzen zurückgibt. Jedes Tool erbt von "
            "minerva.tools.registry.Tool und hat: name (str), description (str), parameters "
            "(JSON-Schema dict) und eine Methode run(self, args, ctx), die "
            "minerva.tools.registry.ToolResult(ok: bool, content: str) zurückgibt. Über ctx "
            "sind ctx.workdir (Path), ctx.cfg (Config), ctx.guard und ctx.emit verfügbar. "
            "Nutze für gefährliche Aktionen ctx.guard.review(...). Schreibe robusten, "
            "fehlertoleranten Code mit Docstrings auf Deutsch.\n\n"
            f"Gewünschte Fähigkeit:\n{spec}\n\n"
            f"Als Ausgangspunkt diese Vorlage (überschreibe die Datei {target}):\n{template}\n\n"
            "Erstelle bzw. überschreibe genau diese Datei und stelle sicher, dass sie ohne "
            "Syntaxfehler importierbar ist. Führe zum Schluss `python -c \"import ast; "
            f"ast.parse(open('{target}').read())\"` aus, um die Syntax zu prüfen."
        )

        ctx.emit("info", f"Claude Code baut Skill '{name}' …")
        timeout = int(args.get("timeout", 900))
        try:
            proc = subprocess.run(
                [claude_bin, "-p", prompt, "--dangerously-skip-permissions"],
                cwd=str(SKILLS_DIR),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=dict(os.environ),
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Claude Code Timeout nach {timeout}s.")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Fehler: {exc}")

        if not target.exists():
            return ToolResult(False, f"Claude Code hat keine Datei {target.name} erzeugt.\n{proc.stdout[-1000:]}")

        code = target.read_text(encoding="utf-8")
        ok, msg = _syntax_ok(code)
        if not ok:
            return ToolResult(False, f"Erzeugter Skill hat Syntaxfehler: {msg}")

        if ctx.skill_manager and ctx.registry:
            count, load_msg = ctx.skill_manager.load_file(target, ctx.registry)
            if count == 0:
                return ToolResult(False, f"Skill geschrieben, Laden fehlgeschlagen: {load_msg}")
            ctx.emit("info", f"Neuer Skill aktiv: {name}")
            return ToolResult(True, f"Skill {name} von Claude Code gebaut und geladen. {load_msg}")
        return ToolResult(True, f"Skill {name} gebaut (Neustart zum Laden nötig).")
