"""RAG-Werkzeuge: Langzeitgedächtnis durchsuchen und befüllen.

Nutzt die RagService-Brücke zum bestehenden RAG-Modul. Damit merkt sich MINERVA
Fakten, Dokumente und frühere Gespräche und kann sie später gezielt abrufen.
"""
from __future__ import annotations

import time

from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult


def _rag(ctx: ToolContext):
    rag = ctx.rag
    if rag is None:
        raise RuntimeError("RAG-Dienst ist nicht eingebunden.")
    return rag


class RagSearchTool(Tool):
    name = "memory_search"
    description = (
        "Durchsucht das Langzeitgedächtnis (RAG) nach relevanten Fakten, Dokumenten und "
        "früheren Gesprächen. Gibt die besten Treffer als Kontext zurück. Nutze dies, wenn "
        "der Nutzer sich auf etwas Früheres bezieht oder gespeichertes Wissen gefragt ist."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Suchanfrage in natürlicher Sprache."},
            "limit": {"type": "integer", "description": "Anzahl Treffer (Default 6)."},
        },
        "required": ["query"],
    }

    def run(self, args, ctx: ToolContext):
        try:
            rag = _rag(ctx)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, str(exc))
        limit = int(args.get("limit", ctx.cfg.get("rag.top_n", 6)))
        ctx.emit("info", "Durchsuche Langzeitgedächtnis …")
        try:
            results = rag.search(args["query"], limit=limit)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"RAG-Suche fehlgeschlagen: {exc}")
        if not results:
            return ToolResult(True, "Keine relevanten Treffer im Gedächtnis.")
        lines = []
        for i, r in enumerate(results, 1):
            score = r.get("score", 0.0)
            content = (r.get("content") or "").strip().replace("\n", " ")
            src = r.get("source_name") or r.get("hierarchy") or ""
            lines.append(f"[{i}] ({score:.3f}) {src}\n{content[:500]}")
        return ToolResult(True, "\n\n".join(lines))


class RagIngestTool(Tool):
    name = "memory_store"
    description = (
        "Speichert einen Text dauerhaft im Langzeitgedächtnis (RAG). Nutze dies, um wichtige "
        "Fakten, Notizen, Vorlieben des Nutzers oder Ergebnisse festzuhalten, die später "
        "wieder abrufbar sein sollen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Der zu speichernde Inhalt."},
            "document_id": {"type": "string", "description": "Eindeutige ID (gleiche ID = Versionierung)."},
            "title": {"type": "string", "description": "Optionaler Titel/Quellname."},
        },
        "required": ["text", "document_id"],
    }

    def run(self, args, ctx: ToolContext):
        try:
            rag = _rag(ctx)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, str(exc))
        decision = ctx.guard.review("rag_store", "Ins Gedächtnis schreiben", args.get("document_id", ""), Risk.SAFE)
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        try:
            rag.ingest_text(
                args["text"],
                document_id=args["document_id"],
                metadata={"stored_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "minerva"},
                source_name=args.get("title", args["document_id"]),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Speichern fehlgeschlagen: {exc}")
        return ToolResult(True, f"Gespeichert unter document_id={args['document_id']}.")
