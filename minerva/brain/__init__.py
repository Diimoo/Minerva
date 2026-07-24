"""Gehirn-Schicht: umschaltbare LLM-Backends mit einheitlichem Tool-Calling."""
from .base import AssistantTurn, LLMBackend, Message, ToolCall, ToolSpec
from .factory import build_backend

__all__ = [
    "AssistantTurn",
    "LLMBackend",
    "Message",
    "ToolCall",
    "ToolSpec",
    "build_backend",
]
