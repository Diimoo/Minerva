"""Tool-Registry: Verwaltung, Kontext und Dispatch aller Werkzeuge."""
from __future__ import annotations

import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..brain.base import ToolSpec
from ..config import Config
from ..safety import Guard

log = logging.getLogger("minerva.tools")

# Callback zum Loggen/Anzeigen von Tool-Aktivität in der UI.
EmitFn = Callable[[str, str], None]  # (event_type, text)


@dataclass
class ToolContext:
    """Alles, worauf ein Tool zur Laufzeit zugreifen darf."""

    cfg: Config
    guard: Guard
    workdir: Path
    emit: EmitFn = lambda et, txt: None
    # Von der App nachträglich gesetzt (Referenzen, um Zyklen zu vermeiden):
    backend: Any = None          # LLMBackend (für Selbstverbesserung)
    skill_manager: Any = None    # SkillManager (Hot-Reload)
    registry: Any = None         # ToolRegistry (für Skill-Registrierung)
    rag: Any = None              # RAG-Wrapper (lazy)
    memories: Any = None         # MemoryStore (persönliches Notizgedächtnis)
    app: Any = None              # JarvisApp (für Selbst-Upgrade/Neustart)
    extras: dict = field(default_factory=dict)

    def set_workdir(self, path: str | Path) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = (self.workdir / p).resolve()
        self.workdir = p
        return p


@dataclass
class ToolResult:
    ok: bool
    content: str

    def __str__(self) -> str:  # was das Modell als Tool-Ausgabe sieht
        prefix = "" if self.ok else "[FEHLER] "
        return prefix + self.content


class Tool:
    """Basisklasse für ein Werkzeug."""

    name: str = "tool"
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            log.debug("Tool %s wird ersetzt.", tool.name)
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [t.spec() for t in self._tools.values()]

    def dispatch(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"Unbekanntes Tool: {name!r}. Verfügbar: {', '.join(self.names())}")
        t0 = time.time()
        ctx.emit("tool_call", f"{name}({_short_args(args)})")
        try:
            result = tool.run(args or {}, ctx)
        except Exception as exc:  # noqa: BLE001
            log.error("Tool %s crashte: %s\n%s", name, exc, traceback.format_exc())
            result = ToolResult(False, f"Tool {name} warf eine Ausnahme: {exc}")
        dt = time.time() - t0
        ctx.emit("tool_result", f"{name} → {'ok' if result.ok else 'fehler'} ({dt:.1f}s)")
        return result


def _short_args(args: dict, limit: int = 160) -> str:
    try:
        import json

        s = json.dumps(args, ensure_ascii=False)
    except Exception:
        s = str(args)
    return s if len(s) <= limit else s[:limit] + "…"


def build_default_registry(cfg: Config) -> ToolRegistry:
    """Registriert alle standardmäßig aktiven Tools laut Konfiguration."""
    reg = ToolRegistry()

    from .files import ListDirTool, ReadFileTool, WriteFileTool
    from .python_exec import PythonEvalTool

    reg.register(ReadFileTool())
    reg.register(WriteFileTool())
    reg.register(ListDirTool())
    reg.register(PythonEvalTool())

    if cfg.get("tools.shell_enabled", True):
        from .shell import ShellTool

        reg.register(ShellTool())

    if cfg.get("tools.computer_control_enabled", True):
        from .computer import (
            ClickTool,
            ScreenshotTool,
            TypeTextTool,
            KeyPressTool,
            ClipboardTool,
            NotifyTool,
            OpenAppTool,
            VolumeTool,
        )

        reg.register(ScreenshotTool())
        reg.register(ClickTool())
        reg.register(TypeTextTool())
        reg.register(KeyPressTool())
        reg.register(ClipboardTool())
        reg.register(NotifyTool())
        reg.register(OpenAppTool())
        reg.register(VolumeTool())

    if cfg.get("tools.claude_code_enabled", True):
        from .claude_code import ClaudeCodeTool

        reg.register(ClaudeCodeTool())

    if cfg.get("tools.rag_enabled", True):
        from .rag import RagIngestTool, RagSearchTool

        reg.register(RagSearchTool())
        reg.register(RagIngestTool())

    if cfg.get("memories.enabled", True):
        from .memories import ForgetTool, RecallTool, RememberTool

        reg.register(RememberTool())
        reg.register(RecallTool())
        reg.register(ForgetTool())

    if cfg.get("tools.web_enabled", False):
        from .web import WebFetchTool

        reg.register(WebFetchTool())

    if cfg.get("tools.self_improve_enabled", True):
        from .selfimprove import (
            CreateSkillTool,
            ListSkillsTool,
            ReloadSkillsTool,
            DelegateToClaudeCodeTool,
        )
        from .selfupgrade import SelfUpgradeTool

        reg.register(CreateSkillTool())
        reg.register(ListSkillsTool())
        reg.register(ReloadSkillsTool())
        reg.register(DelegateToClaudeCodeTool())
        reg.register(SelfUpgradeTool())

    return reg
