"""Gemeinsame Fixtures für die MINERVA-Testsuite."""
from __future__ import annotations

from pathlib import Path

import pytest

from minerva.config import Config
from minerva.safety.guard import Guard
from minerva.tools.registry import ToolContext

# Sperrliste in der Form, die der Nutzer real konfiguriert hat.
REAL_DENYLIST = [
    "rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf $HOME",
    "mkfs", "dd if=", ":(){ :|:& };:", "> /dev/sda",
    "shutdown", "reboot", "poweroff",
]


@pytest.fixture
def make_guard():
    """Guard-Fabrik. Audit aus, damit Tests nicht ins echte Log schreiben."""
    def _make(mode: str = "guarded", denylist: list[str] | None = None,
              confirm: bool | None = None) -> Guard:
        g = Guard(
            mode=mode,
            hard_denylist=REAL_DENYLIST if denylist is None else denylist,
            audit=False,
        )
        if confirm is not None:
            g.set_confirm_fn(lambda title, detail, risk: confirm)
        return g
    return _make


@pytest.fixture
def make_ctx(tmp_path: Path):
    """ToolContext-Fabrik mit temporärem Arbeitsverzeichnis."""
    def _make(guard: Guard) -> ToolContext:
        return ToolContext(cfg=Config({}), guard=guard, workdir=tmp_path)
    return _make


def allowed(guard: Guard, command: str) -> bool:
    """Endgültige Entscheidung des Guards für einen Shell-Befehl."""
    risk, why = guard.classify_shell(command)
    return guard.review("shell", "test", command, risk).allowed
