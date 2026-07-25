"""Erzeugt das passende Gehirn anhand der Konfiguration."""
from __future__ import annotations

import logging

from ..config import Config, resolve_brain_backend
from .base import LLMBackend

log = logging.getLogger("minerva.brain")


def build_backend(cfg: Config) -> LLMBackend:
    backend = resolve_brain_backend(cfg)

    if backend == "anthropic":
        from .anthropic_backend import AnthropicBackend

        log.info("Gehirn: Anthropic (%s)", cfg.get("brain.anthropic_model"))
        return AnthropicBackend(
            model=cfg.get("brain.anthropic_model", "claude-fable-5"),
            temperature=cfg.get("brain.temperature", 0.4),
            max_tokens=cfg.get("brain.max_tokens", 2048),
        )

    if backend == "claude_code":
        from .claude_code_backend import ClaudeCodeBackend

        model = cfg.get("brain.claude_code_model") or None
        log.info("Gehirn: Claude Code über Abo (%s)", model or "CLI-Vorgabe")
        return ClaudeCodeBackend(
            model=model,
            effort=cfg.get("brain.claude_code_effort") or None,
            timeout=cfg.get("brain.claude_code_timeout", 300),
        )

    from .ollama_backend import OllamaBackend

    log.info("Gehirn: Ollama (%s)", cfg.get("brain.model"))
    return OllamaBackend(
        model=cfg.get("brain.model", "qwen2.5-coder:14b-instruct"),
        host=cfg.get("brain.ollama_host", "http://127.0.0.1:11434"),
        temperature=cfg.get("brain.temperature", 0.4),
        num_ctx=cfg.get("brain.num_ctx", 16384),
        max_tokens=cfg.get("brain.max_tokens", 2048),
    )
