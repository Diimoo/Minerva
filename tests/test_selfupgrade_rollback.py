"""F10: Ein fehlgeschlagener Rollback darf nicht als erfolgreicher gemeldet werden.

`_restore()` schluckte jede Ausnahme (`except Exception: pass`), während der
Aufrufer dem Nutzer „automatisch zurückgerollt" meldete. Auf dem
sicherheitskritischsten Pfad des Projekts — Minerva hat gerade ihren eigenen
Code überschrieben und der Selbsttest ist fehlgeschlagen — war die Meldung
damit potenziell eine Lüge.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from minerva.tools import selfupgrade


def test_restore_reports_failure(monkeypatch, tmp_path):
    """F10: Scheitert die Übernahme, muss _restore das melden."""
    def boom(src: Path, dst: Path) -> None:
        raise OSError("Zielverzeichnis nicht beschreibbar")

    monkeypatch.setattr(selfupgrade, "_sync_into", boom)
    assert selfupgrade._restore(tmp_path / "backup", tmp_path / "ziel") is False


def test_restore_reports_success(monkeypatch, tmp_path):
    """Kein Kollateralschaden: der Erfolgsfall meldet Erfolg."""
    called: list[tuple[Path, Path]] = []

    def fine(src: Path, dst: Path) -> None:
        called.append((src, dst))

    monkeypatch.setattr(selfupgrade, "_sync_into", fine)
    assert selfupgrade._restore(tmp_path / "backup", tmp_path / "ziel") is True
    assert called, "_sync_into wurde nicht aufgerufen"


def test_restore_does_not_raise(monkeypatch, tmp_path):
    """_restore bleibt fehlertolerant — es soll melden, nicht sprengen.

    Der Aufrufer steckt schon im Fehlerfall; eine zweite Ausnahme würde die
    Rollback-Meldung ganz verhindern.
    """
    monkeypatch.setattr(
        selfupgrade, "_sync_into",
        lambda src, dst: (_ for _ in ()).throw(RuntimeError("kaputt")),
    )
    selfupgrade._restore(tmp_path / "a", tmp_path / "b")   # darf nicht werfen
