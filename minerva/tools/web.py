"""Web-Werkzeug: eine URL abrufen (optional, standardmäßig deaktiviert)."""
from __future__ import annotations

import re

from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult

MAX = 12_000


def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Ruft eine URL ab und gibt den (bereinigten) Textinhalt zurück. Für Recherche/aktuelle Infos."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "raw": {"type": "boolean", "description": "true = HTML unbereinigt zurückgeben."},
        },
        "required": ["url"],
    }

    def run(self, args, ctx: ToolContext):
        url = args.get("url", "")
        if not re.match(r"^https?://", url):
            return ToolResult(False, "Nur http(s)-URLs erlaubt.")
        decision = ctx.guard.review("web", "URL abrufen", url, Risk.MODERATE)
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        try:
            import httpx

            r = httpx.get(url, follow_redirects=True, timeout=25,
                          headers={"User-Agent": "MINERVA/0.1 (+local assistant)"})
            r.raise_for_status()
            body = r.text
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Abruf fehlgeschlagen: {exc}")
        text = body if args.get("raw") else _strip_html(body)
        if len(text) > MAX:
            text = text[:MAX] + "\n… [gekürzt]"
        return ToolResult(True, f"{url}\n\n{text}")
