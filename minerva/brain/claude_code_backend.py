"""Gehirn über Claude Code (Agent SDK) — läuft unter dem Pro/Max-Abo.

Kein ANTHROPIC_API_KEY nötig: das Agent SDK erbt die Authentifizierung von der
`claude` CLI, also vom OAuth-Login des Abos. Damit ist Claude als Gehirn nutzbar,
ohne die usage-abgerechnete Developer-Platform-API zu berühren.

Sicherheitsarchitektur — der entscheidende Punkt
------------------------------------------------
Das Agent SDK würde die Agent-Loop normalerweise selbst besitzen und Werkzeuge
selbst ausführen. Das wäre hier falsch: MINERVAs Werkzeuge (`shell`,
`python_exec`, `computer`) laufen über `ToolRegistry.dispatch()`, und *dort*
sitzt `Guard.review()`. Würde das SDK ausführen, fiele der Guard aus dem Pfad.

Deshalb:
  * `tools=[]`            — alle eingebauten SDK-Werkzeuge (Bash, Read, Edit …)
                            sind abgeschaltet. Das SDK führt nichts aus.
  * MINERVAs `ToolSpec`s  — werden als In-Process-MCP-Server deklariert, damit
                            das Modell sie *sehen und anfordern* kann.
  * PreToolUse-Hook       — antwortet mit `permissionDecision: "defer"`. Das
                            stoppt den Lauf, und `ResultMessage.deferred_tool_use`
                            trägt den angeforderten Aufruf zum Aufrufer zurück.
  * `complete()`          — gibt ihn als `AssistantTurn.tool_calls` zurück.

Der Orchestrator führt das Werkzeug dann wie bei jedem anderen Backend über die
Registry aus — Guard, Bestätigungsdialoge und HUD-Ereignisse bleiben unverändert.
Der MCP-Handler ist nur ein Netz: er wird nie erreicht, und falls doch, verweigert
er, statt etwas ungeprüft auszuführen.

Zustandslosigkeit
-----------------
Der Orchestrator schickt bei jeder Iteration den vollständigen Verlauf mit
(`memory.history(system)`). Dieses Backend verhält sich passend dazu
zustandslos: es rendert den Verlauf pro Aufruf zu einem Transkript und setzt
`max_turns=1`, damit das SDK keine eigene Mehrschritt-Loop fährt. Das kostet
Kontext-Neuaufbau pro Tool-Iteration; SDK-seitige Session-Fortsetzung wäre eine
mögliche spätere Optimierung, würde aber den Vertrag von `LLMBackend` brechen.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from .base import AssistantTurn, LLMBackend, Message, TokenCallback, ToolCall, ToolSpec

log = logging.getLogger("minerva.brain.claude_code")

# Name des In-Process-MCP-Servers. Werkzeuge erscheinen dem Modell darüber als
# "mcp__<server>__<tool>" — das Präfix wird beim Zurückmelden wieder entfernt.
_MCP_SERVER = "minerva"
_TOOL_PREFIX = f"mcp__{_MCP_SERVER}__"


class ClaudeCodeBackend(LLMBackend):
    name = "claude_code"
    supports_tools = True

    def __init__(
        self,
        model: Optional[str] = None,
        effort: Optional[str] = None,
        cwd: Optional[str] = None,
        timeout: int = 300,
    ) -> None:
        # Import hier, damit MINERVA ohne installiertes SDK startbar bleibt
        # (Ollama-Pfad soll nicht an einer fehlenden Abhängigkeit scheitern).
        import claude_agent_sdk  # noqa: F401  (Verfügbarkeitsprüfung)

        self.model = model
        self.effort = effort
        self.cwd = cwd
        self.timeout = timeout

    # -- Verlauf -> Prompt-Transkript --------------------------------------
    @staticmethod
    def _split_system(messages: list[Message]) -> tuple[str, list[Message]]:
        """Trennt System-Nachrichten vom Gesprächsverlauf."""
        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        rest = [m for m in messages if m.role != "system"]
        return "\n\n".join(system_parts), rest

    @staticmethod
    def _render_transcript(history: list[Message]) -> str:
        """Rendert den Verlauf zu einem eindeutigen Transkript.

        Das SDK nimmt einen Prompt-String, keine Nachrichtenliste. Damit das
        Modell Werkzeug-Ergebnisse als solche erkennt, werden die Rollen
        explizit ausgeschrieben.
        """
        lines: list[str] = []
        for m in history:
            if m.role == "user":
                lines.append(f"[Nutzer]\n{m.content}")
            elif m.role == "assistant":
                if m.content:
                    lines.append(f"[Du]\n{m.content}")
                for c in m.tool_calls:
                    lines.append(f"[Du -> Werkzeug {c.name}]\n{c.arguments}")
            elif m.role == "tool":
                lines.append(f"[Ergebnis von {m.name or 'Werkzeug'}]\n{m.content}")

        transcript = "\n\n".join(lines)

        # Endet der Verlauf auf einem Werkzeug-Ergebnis, gibt es keine frische
        # Nutzer-Anweisung — dann braucht das Modell die Aufforderung, mit den
        # Ergebnissen weiterzuarbeiten.
        if history and history[-1].role == "tool":
            transcript += (
                "\n\n[Anweisung]\nArbeite mit diesen Werkzeug-Ergebnissen weiter. "
                "Nutze ein weiteres Werkzeug, falls nötig, sonst gib die "
                "abschließende Antwort."
            )
        return transcript

    # -- MCP-Deklaration von MINERVAs Werkzeugen ---------------------------
    def _build_mcp_server(self, tools: list[ToolSpec]) -> Any:
        """Deklariert MINERVAs Werkzeuge, ohne sie ausführbar zu machen."""
        import claude_agent_sdk as sdk

        async def _refuse(args: dict[str, Any]) -> dict[str, Any]:
            # Unerreichbar: der PreToolUse-Hook deferred vorher. Falls dieser
            # Pfad je erreicht wird, ist das ein Fehler — dann lieber
            # verweigern als am Guard vorbei ausführen.
            log.error("MCP-Handler erreicht — Defer-Hook hat nicht gegriffen!")
            return {
                "content": [
                    {
                        "type": "text",
                        "text": "Ausführung abgelehnt: Werkzeuge laufen über MINERVAs Guard.",
                    }
                ],
                "isError": True,
            }

        sdk_tools = []
        for spec in tools:
            decorate = sdk.tool(spec.name, spec.description, spec.parameters)
            sdk_tools.append(decorate(_refuse))

        return sdk.create_sdk_mcp_server(name=_MCP_SERVER, tools=sdk_tools)

    # -- Hook: Werkzeugaufruf zurück an MINERVA ----------------------------
    @staticmethod
    def _defer_hook():
        async def hook(
            input_data: dict[str, Any], tool_use_id: Optional[str], context: Any
        ) -> dict[str, Any]:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "defer",
                    "permissionDecisionReason": (
                        "MINERVA führt Werkzeuge selbst aus (Guard-geprüft)."
                    ),
                }
            }

        return hook

    # -- Hauptaufruf -------------------------------------------------------
    def complete(
        self,
        messages: list[Message],
        tools: Optional[list[ToolSpec]] = None,
        on_token: TokenCallback = None,
    ) -> AssistantTurn:
        system, history = self._split_system(messages)
        prompt = self._render_transcript(history)
        specs = tools or []

        try:
            return asyncio.run(
                self._run(prompt, system, specs, on_token)
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Claude-Code-Aufruf fehlgeschlagen: %s", exc)
            raise

    async def _run(
        self,
        prompt: str,
        system: str,
        specs: list[ToolSpec],
        on_token: TokenCallback,
    ) -> AssistantTurn:
        import claude_agent_sdk as sdk

        options_kwargs: dict[str, Any] = {
            # Eingebaute SDK-Werkzeuge komplett aus: MINERVAs Guard führt aus.
            "tools": [],
            "system_prompt": system or None,
            "max_turns": 1,
            # Nur unsere Deklaration, keine fremden MCP-Server aus
            # ~/.claude.json oder Projekt-.mcp.json einsammeln.
            "strict_mcp_config": True,
            "include_partial_messages": True,
        }
        if self.model:
            options_kwargs["model"] = self.model
        if self.effort:
            options_kwargs["effort"] = self.effort
        if self.cwd:
            options_kwargs["cwd"] = self.cwd

        if specs:
            options_kwargs["mcp_servers"] = {_MCP_SERVER: self._build_mcp_server(specs)}
            options_kwargs["hooks"] = {
                "PreToolUse": [
                    sdk.HookMatcher(
                        matcher=f"{_TOOL_PREFIX}.*",
                        hooks=[self._defer_hook()],
                    )
                ]
            }

        options = sdk.ClaudeAgentOptions(**options_kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        streamed = False

        async def _drive() -> None:
            nonlocal streamed
            async for msg in sdk.query(prompt=prompt, options=options):
                # Token-Streaming für TTS/HUD.
                if isinstance(msg, sdk.StreamEvent):
                    delta = self._text_delta(msg.event)
                    if delta:
                        streamed = True
                        if on_token:
                            on_token(delta)
                    continue

                if isinstance(msg, sdk.AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, sdk.TextBlock) and block.text:
                            text_parts.append(block.text)
                    continue

                if isinstance(msg, sdk.ResultMessage):
                    deferred = msg.deferred_tool_use
                    if deferred is not None:
                        tool_calls.append(
                            ToolCall(
                                name=self._strip_prefix(deferred.name),
                                arguments=deferred.input or {},
                                id=deferred.id,
                            )
                        )
                    if msg.is_error:
                        log.warning(
                            "Claude Code meldete Fehler (subtype=%s, status=%s)",
                            msg.subtype,
                            msg.api_error_status,
                        )

        try:
            await asyncio.wait_for(_drive(), timeout=self.timeout)
        except asyncio.TimeoutError:
            log.error("Claude Code überschritt %ss", self.timeout)
            raise
        except Exception as exc:  # noqa: BLE001
            # Das SDK wirft u. a. beim Erreichen von max_turns. Haben wir bis
            # dahin Text oder einen Werkzeugwunsch gesammelt, ist das brauchbar
            # — dann lieber unvollständig weiterarbeiten als die Anfrage
            # verlieren (MINERVA würde sonst nur "Fehler beim Denken" sprechen).
            # Ohne jedes Teilergebnis bleibt es ein echter Fehler.
            if not text_parts and not tool_calls:
                raise
            log.warning("Claude Code brach ab (%s) — nutze Teilergebnis.", exc)

        text = "".join(text_parts).strip()
        if streamed and not text:
            # Gestreamt, aber keine AssistantMessage-Blöcke gesehen: nichts
            # erfinden — der Orchestrator hat eine Leer-Antwort-Behandlung.
            log.debug("Text kam nur über StreamEvents, keine Blöcke gesammelt.")

        return AssistantTurn(text=text, tool_calls=tool_calls)

    # -- Hilfen ------------------------------------------------------------
    @staticmethod
    def _strip_prefix(name: str) -> str:
        return name[len(_TOOL_PREFIX):] if name.startswith(_TOOL_PREFIX) else name

    @staticmethod
    def _text_delta(event: dict[str, Any]) -> str:
        """Zieht Text-Deltas aus einem rohen Anthropic-Stream-Event."""
        if event.get("type") != "content_block_delta":
            return ""
        delta = event.get("delta") or {}
        if delta.get("type") == "text_delta":
            return delta.get("text") or ""
        return ""
