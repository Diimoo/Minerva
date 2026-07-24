"""Persönliches Langzeit-Notizgedächtnis („Memories").

Minerva schreibt Fakten, Präferenzen und Daten über den Nutzer als Markdown-
Dateien in einen Ordner (Default ~/.minerva/memories). Beim Start wird ein
kompakter Auszug in den System-Prompt geladen, damit Minerva den Nutzer über
Neustarts hinweg „kennt". Optional werden Memories zusätzlich ins RAG indexiert.

Bewusst datei-basiert (robust, menschenlesbar, versionierbar) — unabhängig von
laufenden Diensten.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("minerva.memories")

_SLUG = re.compile(r"[^a-z0-9]+")

VALID_CATEGORIES = {"preference", "fact", "person", "project", "instruction", "misc"}


def _slugify(text: str) -> str:
    s = _SLUG.sub("-", text.lower()).strip("-")
    return (s or "notiz")[:60]


class MemoryStore:
    def __init__(self, directory: str | Path, max_inject_chars: int = 4000) -> None:
        self.dir = Path(directory).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_inject_chars = max_inject_chars

    # -- Schreiben ---------------------------------------------------------
    def save(self, content: str, title: Optional[str] = None,
             category: str = "fact", mode: str = "append") -> Path:
        category = category if category in VALID_CATEGORIES else "misc"
        title = (title or content[:40]).strip()
        slug = _slugify(title)
        path = self.dir / f"{slug}.md"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        if path.exists() and mode == "append":
            existing = path.read_text(encoding="utf-8")
            body = existing.rstrip() + f"\n- ({now}) {content.strip()}\n"
            path.write_text(body, encoding="utf-8")
        else:
            header = (
                f"---\ntitle: {title}\ncategory: {category}\n"
                f"created: {now}\nupdated: {now}\n---\n\n"
            )
            path.write_text(header + f"- ({now}) {content.strip()}\n", encoding="utf-8")
        return path

    def delete(self, title: str) -> bool:
        path = self.dir / f"{_slugify(title)}.md"
        if path.exists():
            path.unlink()
            return True
        return False

    # -- Lesen -------------------------------------------------------------
    def _parse(self, path: Path) -> tuple[dict, str]:
        text = path.read_text(encoding="utf-8", errors="replace")
        meta: dict = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                for line in parts[1].strip().splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        meta[k.strip()] = v.strip()
                body = parts[2].strip()
        return meta, body

    def all(self) -> list[tuple[Path, dict, str]]:
        out = []
        for path in sorted(self.dir.glob("*.md")):
            if path.name.startswith("_"):
                continue
            meta, body = self._parse(path)
            out.append((path, meta, body))
        return out

    def search(self, query: str) -> list[tuple[str, str]]:
        q = query.lower()
        hits = []
        for _path, meta, body in self.all():
            title = meta.get("title", "")
            if q in title.lower() or q in body.lower():
                hits.append((title, body))
        return hits

    def context_block(self) -> str:
        """Kompakter Auszug fürs System-Prompt (nach Kategorie gruppiert, gekappt)."""
        items = self.all()
        if not items:
            return ""
        by_cat: dict[str, list[str]] = {}
        for _path, meta, body in items:
            cat = meta.get("category", "misc")
            title = meta.get("title", "")
            # Bullet-Zeilen zusammenfassen
            lines = [ln.strip("- ").strip() for ln in body.splitlines() if ln.strip().startswith("-")]
            summary = "; ".join(lines) if lines else body.replace("\n", " ")
            by_cat.setdefault(cat, []).append(f"{title}: {summary}")
        parts = ["Was du über den Nutzer weißt (aus deinem Gedächtnis):"]
        for cat in ("instruction", "preference", "person", "project", "fact", "misc"):
            if cat in by_cat:
                parts.append(f"[{cat}] " + " | ".join(by_cat[cat]))
        block = "\n".join(parts)
        if len(block) > self.max_inject_chars:
            block = block[: self.max_inject_chars] + " …"
        return block

    def count(self) -> int:
        return len(list(self.dir.glob("*.md")))
