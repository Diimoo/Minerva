"""Lokales Gehirn über Ollama (kein API-Key nötig)."""
from __future__ import annotations

import logging
from typing import Any, Optional

from .base import AssistantTurn, LLMBackend, Message, TokenCallback, ToolCall, ToolSpec

log = logging.getLogger("minerva.brain.ollama")


class OllamaBackend(LLMBackend):
    name = "ollama"

    def __init__(
        self,
        model: str,
        host: str = "http://127.0.0.1:11434",
        temperature: float = 0.4,
        num_ctx: int = 16384,
        max_tokens: int = 2048,
    ) -> None:
        import ollama

        self.model = model
        self.host = host
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.max_tokens = max_tokens
        self.client = ollama.Client(host=host)

    # -- Nachrichten in Ollama-Format übersetzen ---------------------------
    def _to_ollama_messages(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "content": m.content,
                        # Ollama nutzt den Toolnamen zur Zuordnung.
                        "tool_name": m.name or "",
                    }
                )
                continue
            d: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_calls:
                d["tool_calls"] = [
                    {
                        "function": {
                            "name": c.name,
                            "arguments": c.arguments,
                        }
                    }
                    for c in m.tool_calls
                ]
            out.append(d)
        return out

    def complete(
        self,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        on_token: TokenCallback = None,
    ) -> AssistantTurn:
        ol_messages = self._to_ollama_messages(messages)
        ol_tools = [t.to_ollama() for t in tools] if tools else None

        options = {
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "num_predict": self.max_tokens,
        }

        # Streaming, damit Text live an UI/TTS geht; Tool-Calls kommen im Chunk.
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        try:
            stream = self.client.chat(
                model=self.model,
                messages=ol_messages,
                tools=ol_tools,
                options=options,
                stream=True,
            )
            for chunk in stream:
                msg = chunk.get("message") if isinstance(chunk, dict) else getattr(chunk, "message", None)
                if not msg:
                    continue
                content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", "")
                if content:
                    text_parts.append(content)
                    if on_token:
                        on_token(content)
                raw_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
                if raw_calls:
                    for rc in raw_calls:
                        fn = rc.get("function") if isinstance(rc, dict) else getattr(rc, "function", None)
                        if not fn:
                            continue
                        fname = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
                        fargs = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", None)
                        if fname:
                            tool_calls.append(ToolCall(name=fname, arguments=self._coerce_args(fargs)))
        except Exception as exc:  # noqa: BLE001
            log.error("Ollama-Aufruf fehlgeschlagen: %s", exc)
            raise

        text = "".join(text_parts).strip()

        # Fallback: Manche Modelle (z. B. reine Coder-Modelle) geben Tool-Calls
        # als JSON-Text im content aus, statt strukturiert. Wenn Tools erlaubt
        # waren, aber kein struktureller Call kam, versuchen wir zu parsen.
        if not tool_calls and tools and text:
            parsed = self._parse_text_tool_call(text, {t.name for t in tools})
            if parsed is not None:
                return AssistantTurn(text="", tool_calls=[parsed])

        return AssistantTurn(text=text, tool_calls=tool_calls)

    @staticmethod
    def _parse_text_tool_call(text: str, known: set[str]) -> Optional[ToolCall]:
        import json
        import re

        candidate = text.strip()
        # In ```json ...``` gehüllte Blöcke entpacken.
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
        if m:
            candidate = m.group(1)
        if not (candidate.startswith("{") and candidate.endswith("}")):
            return None
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        name = obj.get("name") or obj.get("tool") or obj.get("function")
        args = obj.get("arguments", obj.get("parameters", {}))
        if isinstance(name, str) and name in known:
            return ToolCall(name=name, arguments=args if isinstance(args, dict) else {})
        return None
