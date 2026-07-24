"""Brücke zum bestehenden RAG-Modul (~/Proj/rag-module).

Das RAG-Modul ist vollständig asynchron und an eine Event-Loop gebunden. Diese
Brücke besitzt eine eigene Loop in einem Hintergrund-Thread und stellt der
restlichen (synchronen) MINERVA-Welt einfache Methoden bereit. Die Initialisierung
ist lazy und fehlertolerant: Ohne installiertes RAG-Modul bleibt MINERVA lauffähig.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import RAG_MODULE_PATH
from .config import Config

log = logging.getLogger("minerva.rag")


class RagService:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._rag = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = False
        self._error: Optional[str] = None
        self._lock = threading.Lock()

    # -- Lifecycle ---------------------------------------------------------
    def _start_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop = asyncio.new_event_loop()

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, name="minerva-rag-loop", daemon=True)
        self._thread.start()

    def _submit(self, coro) -> Any:
        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    def _ensure_qdrant(self, url: str) -> str:
        """Stellt sicher, dass der Qdrant-Server läuft (startet Docker-Container bei
        Bedarf). Gibt die effektive URL zurück (Fallback ':memory:', falls nicht
        erreichbar)."""
        if not url.startswith("http"):
            return url
        if self._qdrant_reachable(url):
            return url
        if not self.cfg.get("rag.qdrant_autostart_docker", True) or not shutil.which("docker"):
            log.warning("Qdrant nicht erreichbar und kein Docker-Autostart -> :memory:")
            return ":memory:"
        name = self.cfg.get("rag.qdrant_container", "minerva-qdrant")
        data_dir = Path(self.cfg.get("rag.qdrant_data_dir", "~/.minerva/qdrant")).expanduser()
        port = url.rsplit(":", 1)[-1].split("/")[0]
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            existing = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}"],
                capture_output=True, text=True,
            ).stdout.strip()
            if existing == name:
                subprocess.run(["docker", "start", name], capture_output=True, text=True)
            else:
                subprocess.run(
                    ["docker", "run", "-d", "--name", name, "-p", f"{port}:6333",
                     "-v", f"{data_dir}:/qdrant/storage", "--restart", "unless-stopped",
                     "qdrant/qdrant"],
                    capture_output=True, text=True,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("Qdrant-Docker-Start fehlgeschlagen: %s", exc)
            return ":memory:"
        deadline = time.time() + 30
        while time.time() < deadline:
            if self._qdrant_reachable(url):
                return url
            time.sleep(1.0)
        log.warning("Qdrant kam nicht hoch -> :memory:")
        return ":memory:"

    @staticmethod
    def _qdrant_reachable(url: str) -> bool:
        try:
            import httpx

            r = httpx.get(url.rstrip("/") + "/readyz", timeout=2.5)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_ready(self) -> bool:
        """Initialisiert das RAG-Modul beim ersten Aufruf. Gibt Erfolg zurück."""
        with self._lock:
            if self._ready:
                return True
            if self._error:
                return False
            try:
                if str(RAG_MODULE_PATH) not in sys.path and RAG_MODULE_PATH.exists():
                    sys.path.insert(0, str(RAG_MODULE_PATH))
                from rag_module import AdvancedRAGModule, RAGSettings  # type: ignore

                effective_url = self._ensure_qdrant(self.cfg.get("rag.qdrant_url", ":memory:"))
                settings = RAGSettings(
                    qdrant_url=effective_url,
                    collection_name=self.cfg.get("rag.collection", "minerva_memory"),
                    dense_backend=self.cfg.get("rag.dense_backend", "fastembed"),
                    sparse_backend=self.cfg.get("rag.sparse_backend", "fastembed_bm25"),
                    rerank_backend=self.cfg.get("rag.rerank_backend", "fastembed"),
                )
                self._start_loop()
                self._rag = self._submit(self._make_module(AdvancedRAGModule, settings))
                self._ready = True
                log.info("RAG-Modul initialisiert (collection=%s).", settings.collection_name)
                return True
            except Exception as exc:  # noqa: BLE001
                self._error = str(exc)
                log.warning("RAG nicht verfügbar: %s", exc)
                return False

    @staticmethod
    async def _make_module(cls, settings):
        return cls(settings=settings)

    # -- Öffentliche, synchrone API ---------------------------------------
    def search(self, query: str, limit: int = 6, metadata_filter: Optional[dict] = None) -> list[dict]:
        if not self.ensure_ready():
            raise RuntimeError(f"RAG nicht verfügbar: {self._error}")
        return self._submit(self._rag.retrieve(query, limit=limit, metadata_filter=metadata_filter))

    def ingest_text(self, text: str, document_id: str, metadata: Optional[dict] = None,
                    document_type: str = "markdown", source_name: Optional[str] = None) -> Any:
        if not self.ensure_ready():
            raise RuntimeError(f"RAG nicht verfügbar: {self._error}")
        meta = dict(metadata or {})
        meta.setdefault("document_id", document_id)
        return self._submit(
            self._rag.ingest_text(
                text,
                document_type=document_type,
                metadata=meta,
                source_name=source_name or f"{document_id}.md",
            )
        )

    def ingest_document(self, path: str, document_id: str, document_type: str = "markdown",
                        metadata: Optional[dict] = None) -> Any:
        if not self.ensure_ready():
            raise RuntimeError(f"RAG nicht verfügbar: {self._error}")
        meta = dict(metadata or {})
        meta.setdefault("document_id", document_id)
        return self._submit(
            self._rag.ingest_document(path, document_type=document_type, metadata=meta)
        )

    @property
    def available(self) -> bool:
        return self._ready

    @property
    def error(self) -> Optional[str]:
        return self._error

    def close(self) -> None:
        if self._rag is not None and self._loop is not None:
            try:
                self._submit(self._rag.close())
            except Exception:  # noqa: BLE001
                pass
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
