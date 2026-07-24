"""Dynamisches Skill-System — das Herz der Selbstverbesserung.

Ein *Skill* ist eine Python-Datei in ``~/.minerva/skills/``, die eine Funktion
``get_tools() -> list[Tool]`` bereitstellt. Der SkillManager lädt alle Skills,
registriert ihre Tools in der Registry und kann sie zur Laufzeit **heiß neu laden**.

Damit kann MINERVA neue Fähigkeiten schreiben (selbst oder via Claude Code),
validieren und sofort nutzen — ohne Neustart.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from .. import SKILLS_DIR

if TYPE_CHECKING:
    from ..tools.registry import Tool, ToolRegistry

log = logging.getLogger("minerva.skills")

SKILL_TEMPLATE = '''"""MINERVA-Skill: {title}

{description}

Regeln:
  * get_tools() muss eine Liste von Tool-Instanzen zurückgeben.
  * Ein Tool hat: name, description, parameters (JSON-Schema) und run(args, ctx).
  * run(args, ctx) gibt ToolResult(ok: bool, content: str) zurück.
  * ctx.workdir, ctx.cfg, ctx.guard, ctx.emit stehen zur Verfügung.
"""
from minerva.tools.registry import Tool, ToolResult


class {classname}(Tool):
    name = "{tool_name}"
    description = "{tool_desc}"
    parameters = {parameters}

    def run(self, args, ctx):
        # TODO: Implementierung
        return ToolResult(True, "noch nicht implementiert")


def get_tools():
    return [{classname}()]
'''


class SkillManager:
    def __init__(self) -> None:
        # tool-name -> herkunfts-modulname, um beim Reload sauber abzuräumen.
        self._skill_tool_names: set[str] = set()
        self._loaded_modules: list[str] = []

    def _module_name(self, path: Path) -> str:
        return f"minerva_skill_{path.stem}"

    def load_file(self, path: Path, registry: "ToolRegistry") -> tuple[int, str]:
        """Lädt eine einzelne Skill-Datei. Gibt (anzahl_tools, meldung) zurück."""
        modname = self._module_name(path)
        try:
            spec = importlib.util.spec_from_file_location(modname, path)
            if spec is None or spec.loader is None:
                return 0, f"Kann Skill nicht laden: {path.name}"
            module = importlib.util.module_from_spec(spec)
            sys.modules[modname] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            log.error("Skill %s fehlgeschlagen: %s\n%s", path.name, exc, traceback.format_exc())
            sys.modules.pop(modname, None)
            return 0, f"Fehler beim Laden von {path.name}: {exc}"

        get_tools = getattr(module, "get_tools", None)
        if not callable(get_tools):
            return 0, f"{path.name}: keine get_tools()-Funktion."
        try:
            tools = get_tools()
        except Exception as exc:  # noqa: BLE001
            return 0, f"{path.name}: get_tools() warf {exc}"

        count = 0
        for tool in tools:
            registry.register(tool)
            self._skill_tool_names.add(tool.name)
            count += 1
        if modname not in self._loaded_modules:
            self._loaded_modules.append(modname)
        return count, f"{path.name}: {count} Tool(s) geladen."

    def load_all(self, registry: "ToolRegistry") -> list[str]:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        messages: list[str] = []
        for path in sorted(SKILLS_DIR.glob("*.py")):
            if path.name.startswith("_"):
                continue
            _, msg = self.load_file(path, registry)
            messages.append(msg)
        return messages

    def reload(self, registry: "ToolRegistry") -> list[str]:
        # Alte Skill-Tools abmelden.
        for name in list(self._skill_tool_names):
            registry.unregister(name)
        self._skill_tool_names.clear()
        # Modul-Cache leeren, damit Änderungen greifen.
        for modname in list(self._loaded_modules):
            sys.modules.pop(modname, None)
        self._loaded_modules.clear()
        return self.load_all(registry)

    def list_skills(self) -> list[Path]:
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        return sorted(p for p in SKILLS_DIR.glob("*.py") if not p.name.startswith("_"))
