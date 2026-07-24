"""Gehirn über die Anthropic-API (benötigt ANTHROPIC_API_KEY)."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .base import AssistantTurn, LLMBackend, Message, TokenCallback, ToolCall, ToolSpec

log = logging.getLogger("minerva.brain.anthropic")


class AnthropicBackend(LLMBackend):
    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-fable-5",
        temperature: float = 0.4,
        max_tokens: int = 2048,
        api_key: Optional[str] = None,
    ) -> None:
        import anthropic

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY nicht gesetzt.")
        self.client = anthropic.Anthropic(api_key=key)

    # -- Nachrichten in Anthropic-Format übersetzen ------------------------
    def _split(self, messages: list[Message]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            if m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id or "",
                                "content": m.content,
                            }
                        ],
                    }
                )
                continue
            if m.role == "assistant" and m.tool_calls:
                blocks: list[dict] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for c in m.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": c.id,
                            "name": c.name,
                            "input": c.arguments,
                        }
                    )
                out.append({"role": "assistant", "content": blocks})
                continue
            out.append({"role": m.role, "content": m.content or ""})
        return "\n\n".join(system_parts), out

    def complete(
        self,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        on_token: TokenCallback = None,
    ) -> AssistantTurn:
        system, an_messages = self._split(messages)
        an_tools = [t.to_anthropic() for t in tools] if tools else None

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": an_messages,
        }
        if system:
            kwargs["system"] = system
        if an_tools:
            kwargs["tools"] = an_tools

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        try:
            with self.client.messages.stream(**kwargs) as stream:
                for event in stream.text_stream:
                    if event:
                        text_parts.append(event)
                        if on_token:
                            on_token(event)
                final = stream.get_final_message()
            for block in final.content:
                if block.type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=self._coerce_args(block.input),
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            log.error("Anthropic-Aufruf fehlgeschlagen: %s", exc)
            raise

        return AssistantTurn(text="".join(text_parts).strip(), tool_calls=tool_calls)
