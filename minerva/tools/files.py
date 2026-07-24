"""Datei-Werkzeuge: lesen, schreiben, auflisten."""
from __future__ import annotations

from pathlib import Path

from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult

MAX_READ = 200_000  # Zeichen


def _resolve(ctx: ToolContext, path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (ctx.workdir / p)
    return p.resolve()


class ReadFileTool(Tool):
    name = "read_file"
    description = "Liest den Textinhalt einer Datei. Optional ab Zeile 'offset' für 'limit' Zeilen."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Pfad (relativ zum Arbeitsverzeichnis oder absolut)."},
            "offset": {"type": "integer", "description": "Startzeile (1-basiert), optional."},
            "limit": {"type": "integer", "description": "Anzahl Zeilen, optional."},
        },
        "required": ["path"],
    }

    def run(self, args, ctx):
        p = _resolve(ctx, args["path"])
        if not p.exists():
            return ToolResult(False, f"Datei existiert nicht: {p}")
        if p.is_dir():
            return ToolResult(False, f"Ist ein Verzeichnis, keine Datei: {p}")
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Lesefehler: {exc}")
        offset = args.get("offset")
        limit = args.get("limit")
        if offset or limit:
            lines = text.splitlines()
            start = max(0, (offset or 1) - 1)
            end = start + limit if limit else len(lines)
            text = "\n".join(lines[start:end])
        if len(text) > MAX_READ:
            text = text[:MAX_READ] + f"\n… [gekürzt, {len(text)} Zeichen gesamt]"
        return ToolResult(True, f"{p}:\n{text}")


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Schreibt Text in eine Datei (überschreibt vorhandenen Inhalt). Legt fehlende "
        "Verzeichnisse an. Mit append=true wird angehängt."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "append": {"type": "boolean", "description": "Anhängen statt überschreiben."},
        },
        "required": ["path", "content"],
    }

    def run(self, args, ctx):
        p = _resolve(ctx, args["path"])
        content = args.get("content", "")
        append = bool(args.get("append", False))
        existed = p.exists()
        decision = ctx.guard.review(
            "file_write",
            f"Datei {'anhängen' if append else 'schreiben'}: {p.name}",
            f"{p}  (+{len(content)} Zeichen{', überschreibt vorhandene Datei' if existed and not append else ''})",
            Risk.DANGEROUS if (existed and not append) else Risk.MODERATE,
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(p, mode, encoding="utf-8") as f:
                f.write(content)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Schreibfehler: {exc}")
        return ToolResult(True, f"{'Angehängt an' if append else 'Geschrieben'}: {p} ({len(content)} Zeichen)")


class ListDirTool(Tool):
    name = "list_dir"
    description = "Listet den Inhalt eines Verzeichnisses (Dateien + Unterordner)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Verzeichnis; leer = aktuelles Arbeitsverzeichnis."},
        },
    }

    def run(self, args, ctx):
        p = _resolve(ctx, args.get("path", ".")) if args.get("path") else ctx.workdir
        if not p.exists():
            return ToolResult(False, f"Existiert nicht: {p}")
        if not p.is_dir():
            return ToolResult(False, f"Kein Verzeichnis: {p}")
        try:
            entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Fehler: {exc}")
        lines = []
        for e in entries[:500]:
            marker = "/" if e.is_dir() else ""
            try:
                size = e.stat().st_size if e.is_file() else ""
            except OSError:
                size = ""
            lines.append(f"{e.name}{marker}\t{size}")
        return ToolResult(True, f"{p} ({len(entries)} Einträge):\n" + "\n".join(lines))
