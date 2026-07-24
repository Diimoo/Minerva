"""Konversations-Gedächtnis (Kurzzeit) + optionale Übergabe ans RAG (Langzeit)."""
from __future__ import annotations

import logging
import time
from typing import Optional

from .brain.base import Message

log = logging.getLogger("minerva.memory")


class ConversationMemory:
    """Hält den laufenden Dialog und kürzt ihn, damit er ins Kontextfenster passt."""

    def __init__(self, max_messages: int = 40) -> None:
        self.max_messages = max_messages
        self._messages: list[Message] = []

    def add_user(self, text: str) -> None:
        self._messages.append(Message(role="user", content=text))

    def add_assistant(self, msg: Message) -> None:
        self._messages.append(msg)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self._messages.append(
            Message(role="tool", content=content, tool_call_id=tool_call_id, name=name)
        )

    def add_raw(self, msg: Message) -> None:
        self._messages.append(msg)

    def history(self, system: Message) -> list[Message]:
        """System-Prompt + gekürzte Historie. Kürzt paarweise vom Anfang."""
        msgs = self._messages
        if len(msgs) > self.max_messages:
            # Vom Anfang kürzen, aber nie mitten in einer Tool-Sequenz brechen.
            excess = len(msgs) - self.max_messages
            cut = excess
            while cut < len(msgs) and msgs[cut].role == "tool":
                cut += 1
            msgs = msgs[cut:]
        return [system] + msgs

    def last_exchange_text(self) -> str:
        """Text des letzten User+Assistant-Austauschs (für RAG-Ingest)."""
        parts = []
        for m in self._messages[-6:]:
            if m.role in ("user", "assistant") and m.content:
                parts.append(f"{m.role}: {m.content}")
        return "\n".join(parts)

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        return len(self._messages)
