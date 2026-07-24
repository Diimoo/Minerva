"""Einheitliche LLM-Abstraktion für Ollama und Anthropic.

Der Rest von MINERVA spricht nur mit dieser Schnittstelle und weiß nicht, ob
lokal (Ollama) oder per API (Anthropic) geantwortet wird.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Callback für Token-Streaming an UI/TTS.
TokenCallback = Optional[Callable[[str], None]]


@dataclass
class ToolSpec:
    """Deklaration eines Tools in backend-neutralem JSON-Schema-Format."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)

    def to_ollama(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")


@dataclass
class Message:
    """Eine Konversationsnachricht.

    role: "system" | "user" | "assistant" | "tool"
    Bei assistant-Turns mit Werkzeugaufrufen ist tool_calls gesetzt.
    Bei role=="tool" trägt tool_call_id die Zuordnung zum Aufruf.
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None  # Toolname bei role=="tool"

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments}
                for c in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class AssistantTurn:
    """Ergebnis eines LLM-Aufrufs."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: Any = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LLMBackend:
    """Basisklasse. Backends implementieren `complete`."""

    name: str = "base"
    supports_tools: bool = True

    def complete(
        self,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        on_token: TokenCallback = None,
    ) -> AssistantTurn:
        raise NotImplementedError

    # Hilfsfunktion: Argument-JSON robust parsen (Modelle liefern mal str, mal dict).
    @staticmethod
    def _coerce_args(raw: Any) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                return {}
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                return {"_raw": raw}
        return {}
