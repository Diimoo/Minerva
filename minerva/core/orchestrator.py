"""Agent-Orchestrator: verbindet Gehirn, Werkzeuge und Gedächtnis zum Loop.

Ablauf pro Nutzer-Eingabe:
  1. Eingabe ins Gedächtnis.
  2. LLM antwortet — entweder mit Text (fertig) oder mit Werkzeugaufrufen.
  3. Werkzeuge werden ausgeführt, Ergebnisse zurück ins Gedächtnis.
  4. Wiederholen, bis das LLM ohne Werkzeugaufruf antwortet (oder Iterationslimit).
Der finale Text ist die Antwort, die gesprochen/angezeigt wird.
"""
from __future__ import annotations

import logging
import platform
import time
from datetime import datetime
from typing import Callable, Optional

from ..brain.base import LLMBackend, Message
from ..config import Config
from ..memory import ConversationMemory
from ..tools.registry import ToolContext, ToolRegistry
from .state import AgentState

log = logging.getLogger("minerva.core")

# Callbacks: token-stream (str), event(type,text), state(AgentState)
TokenFn = Callable[[str], None]
EventFn = Callable[[str, str], None]
StateFn = Callable[[AgentState], None]


class Orchestrator:
    def __init__(
        self,
        cfg: Config,
        backend: LLMBackend,
        registry: ToolRegistry,
        tool_context: ToolContext,
        memory: Optional[ConversationMemory] = None,
    ) -> None:
        self.cfg = cfg
        self.backend = backend
        self.registry = registry
        self.ctx = tool_context
        self.memory = memory or ConversationMemory()
        self.max_iter = cfg.get("brain.max_tool_iterations", 12)

    # -- System-Prompt -----------------------------------------------------
    def _system_prompt(self) -> Message:
        p = self.cfg.get("persona.style", "Du bist MINERVA.")
        lang = self.cfg.get("persona.language", "de")
        lang_name = {"de": "Deutsch", "en": "English"}.get(lang, lang)
        tool_lines = []
        for spec in self.registry.specs():
            tool_lines.append(f"  - {spec.name}: {spec.description}")
        tools_desc = "\n".join(tool_lines) if tool_lines else "  (keine)"

        now = datetime.now().strftime("%A, %d.%m.%Y %H:%M")
        env = (
            f"Betriebssystem: {platform.system()} {platform.release()}\n"
            f"Aktuelles Arbeitsverzeichnis: {self.ctx.workdir}\n"
            f"Gehirn-Backend: {self.backend.name}\n"
            f"Datum/Zeit: {now}\n"
            f"Sicherheitsmodus: {self.ctx.guard.mode}"
        )

        # Persönliches Gedächtnis (Memories) in den Prompt einspeisen.
        memory_block = ""
        store = getattr(self.ctx, "memories", None)
        if store is not None:
            try:
                block = store.context_block()
                if block:
                    memory_block = "\n\n" + block
            except Exception:  # noqa: BLE001
                pass

        instructions = (
            f"{p}{memory_block}\n\n"
            f"Antworte standardmäßig auf {lang_name}. Fasse dich in gesprochenen Antworten "
            "kurz und natürlich — der Text wird laut vorgelesen. Vermeide Markdown, Aufzählungs-"
            "zeichen und Code-Blöcke in gesprochenen Antworten; formuliere in Fließtext.\n\n"
            "Du bist ein handlungsfähiger Agent mit Werkzeugen. Wenn eine Aufgabe eine Aktion am "
            "Computer erfordert (Dateien, Shell, Programme starten, Recherche, Screenshots, "
            "Programmierung), NUTZE die passenden Werkzeuge, statt nur darüber zu reden. Für "
            "umfangreiche Programmieraufgaben delegiere an das Werkzeug claude_code. Um dir selbst "
            "neue, dauerhafte Fähigkeiten beizubringen, nutze create_skill oder "
            "build_skill_with_claude_code. Wenn du Wissen brauchst, das gespeichert sein könnte, "
            "nutze memory_search; Wichtiges kannst du mit memory_store dauerhaft merken. "
            "Wenn du etwas Persönliches über den Nutzer lernst (Vorlieben, Namen, Gewohnheiten, "
            "Ziele, Regeln), halte es proaktiv mit dem Werkzeug 'remember' fest.\n\n"
            "Gefährliche Aktionen werden ggf. dem Nutzer zur Bestätigung vorgelegt — plane das ein. "
            "Nach getaner Arbeit gib eine knappe, klare Rückmeldung, was du getan hast oder "
            "herausgefunden hast.\n\n"
            f"Verfügbare Werkzeuge:\n{tools_desc}\n\n"
            f"Umgebung:\n{env}"
        )
        return Message(role="system", content=instructions)

    # -- Haupt-Loop --------------------------------------------------------
    def handle(
        self,
        user_text: str,
        on_token: TokenFn = lambda t: None,
        emit: EventFn = lambda et, txt: None,
        on_state: StateFn = lambda s: None,
    ) -> str:
        self.memory.add_user(user_text)
        final_text = ""
        empty_retries = 0

        for iteration in range(1, self.max_iter + 1):
            on_state(AgentState.THINKING)
            system = self._system_prompt()
            messages = self.memory.history(system)
            specs = self.registry.specs()

            try:
                turn = self.backend.complete(messages, specs, on_token)
            except Exception as exc:  # noqa: BLE001
                log.error("LLM-Fehler: %s", exc)
                emit("error", f"Gehirn-Fehler: {exc}")
                return f"Es gab einen Fehler beim Denken: {exc}"

            if turn.wants_tools:
                # Assistant-Turn mit Werkzeugaufrufen protokollieren.
                self.memory.add_assistant(
                    Message(role="assistant", content=turn.text, tool_calls=turn.tool_calls)
                )
                for call in turn.tool_calls:
                    # Die Registry emittiert selbst 'tool_call'/'tool_result' über ctx.emit.
                    result = self.registry.dispatch(call.name, call.arguments, self.ctx)
                    self.memory.add_tool_result(call.id, call.name, str(result))
                continue

            # Keine Werkzeuge -> fertige Antwort.
            final_text = turn.text.strip()
            if not final_text:
                # Lokale Modelle liefern nach Tool-Nutzung gelegentlich eine leere
                # Antwort. Ein paar Neuversuche (neues Sampling) fangen das ab.
                if empty_retries < 3:
                    empty_retries += 1
                    emit("info", "(leere Antwort — neuer Versuch)")
                    continue
                # Nach mehreren leeren Antworten: knapp bestätigen (meist folgte
                # eine erfolgreiche Werkzeugausführung, die keine Worte mehr brauchte).
                final_text = "Erledigt."
            self.memory.add_assistant(Message(role="assistant", content=final_text))
            break
        else:
            emit("warn", f"Iterationslimit ({self.max_iter}) erreicht.")
            final_text = final_text or "Ich habe das Werkzeug-Limit erreicht, bevor ich fertig war."

        self._maybe_ingest_memory()
        return final_text

    # -- Hilfen ------------------------------------------------------------
    def _maybe_ingest_memory(self) -> None:
        if not self.cfg.get("rag.auto_ingest_conversations", False):
            return
        rag = self.ctx.rag
        if rag is None or not getattr(rag, "available", False):
            return
        try:
            text = self.memory.last_exchange_text()
            if len(text) > 80:
                doc_id = f"conv-{int(time.time())}"
                rag.ingest_text(text, document_id=doc_id,
                                metadata={"kind": "conversation"}, source_name="Gespräch")
        except Exception as exc:  # noqa: BLE001
            log.debug("Auto-Ingest übersprungen: %s", exc)

    @staticmethod
    def _short(args: dict, limit: int = 100) -> str:
        try:
            import json

            s = json.dumps(args, ensure_ascii=False)
        except Exception:
            s = str(args)
        return s if len(s) <= limit else s[:limit] + "…"
