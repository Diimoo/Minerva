"""Werkzeuge für das persönliche Notizgedächtnis (Memories)."""
from __future__ import annotations

from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult


def _store(ctx: ToolContext):
    store = getattr(ctx, "memories", None)
    if store is None:
        raise RuntimeError("Memory-Store ist nicht eingebunden.")
    return store


class RememberTool(Tool):
    name = "remember"
    description = (
        "Merkt sich dauerhaft eine Notiz über den Nutzer (Präferenz, Fakt, Person, Projekt "
        "oder Anweisung). Nutze dies proaktiv, wenn du etwas Wichtiges über den Nutzer lernst, "
        "das später relevant sein könnte — Vorlieben, Namen, Gewohnheiten, Ziele, Regeln. "
        "Diese Notizen bleiben über Neustarts erhalten und stehen dir künftig zur Verfügung."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Die zu merkende Information."},
            "title": {"type": "string", "description": "Kurzer Titel/Thema (gleicher Titel = ergänzen)."},
            "category": {
                "type": "string",
                "enum": ["preference", "fact", "person", "project", "instruction", "misc"],
                "description": "Art der Notiz.",
            },
            "mode": {"type": "string", "enum": ["append", "overwrite"], "description": "Ergänzen oder ersetzen."},
        },
        "required": ["content"],
    }

    def run(self, args, ctx: ToolContext):
        try:
            store = _store(ctx)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, str(exc))
        content = (args.get("content") or "").strip()
        if not content:
            return ToolResult(False, "Kein Inhalt.")
        path = store.save(
            content,
            title=args.get("title"),
            category=args.get("category", "fact"),
            mode=args.get("mode", "append"),
        )
        # optional zusätzlich ins RAG
        if ctx.cfg.get("memories.also_ingest_rag", False) and ctx.rag is not None:
            try:
                if getattr(ctx.rag, "available", False) or ctx.rag.ensure_ready():
                    ctx.rag.ingest_text(content, document_id=f"mem-{path.stem}",
                                        metadata={"kind": "memory"}, source_name=path.stem)
            except Exception:  # noqa: BLE001
                pass
        ctx.emit("info", f"Gemerkt: {path.name}")
        return ToolResult(True, f"Notiz gespeichert unter {path.name}.")


class RecallTool(Tool):
    name = "recall"
    description = (
        "Ruft persönliche Notizen aus dem Gedächtnis ab. Mit 'query' wird gezielt gesucht, "
        "ohne 'query' werden alle Notizen aufgelistet. Die wichtigsten Notizen kennst du bereits "
        "aus deinem Kontext — nutze dies für Details oder gezielte Suche."
    )
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Optionaler Suchbegriff."}},
    }

    def run(self, args, ctx: ToolContext):
        try:
            store = _store(ctx)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, str(exc))
        query = args.get("query")
        if query:
            hits = store.search(query)
            if not hits:
                return ToolResult(True, f"Keine Notiz zu »{query}« gefunden.")
            return ToolResult(True, "\n\n".join(f"● {t}\n{b}" for t, b in hits[:10]))
        items = store.all()
        if not items:
            return ToolResult(True, "Noch keine Notizen im Gedächtnis.")
        lines = []
        for _p, meta, body in items:
            lines.append(f"● [{meta.get('category','misc')}] {meta.get('title','')}\n{body}")
        return ToolResult(True, "\n\n".join(lines[:20]))


class ForgetTool(Tool):
    name = "forget"
    description = "Löscht eine persönliche Notiz anhand ihres Titels."
    parameters = {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }

    def run(self, args, ctx: ToolContext):
        try:
            store = _store(ctx)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, str(exc))
        title = args.get("title", "")
        decision = ctx.guard.review("memory", "Notiz löschen", title, Risk.MODERATE)
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        ok = store.delete(title)
        return ToolResult(ok, f"Notiz »{title}« gelöscht." if ok else f"Keine Notiz »{title}« gefunden.")
