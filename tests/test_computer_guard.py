"""Guard-Abdeckung der Computer-Steuerung (Funde F7, F8, F9).

Vier Werkzeuge in computer.py fragten den Guard überhaupt nicht: see_screen,
clipboard, notify, adjust_volume. Sie liefen damit auch im readonly-Modus und
landeten in keinem Audit-Log.

Die Tests nutzen einen RecordingGuard, der jede Anfrage ABLEHNT und mitschreibt.
Damit sind sie nach dem Fix nebenwirkungsfrei: das Werkzeug bricht ab, bevor es
etwas tut.
"""
from __future__ import annotations

import pytest

from minerva.safety.guard import Guard, GuardDecision, Risk
from minerva.tools.computer import (
    ClipboardTool,
    KeyPressTool,
    NotifyTool,
    ScreenshotTool,
    TypeTextTool,
    VolumeTool,
)


class RecordingGuard(Guard):
    """Lehnt alles ab und protokolliert, was gefragt wurde."""

    def __init__(self) -> None:
        super().__init__(mode="readonly", hard_denylist=[], audit=False)
        self.calls: list[tuple[str, str, Risk]] = []

    def review(self, kind, title, detail, risk):  # type: ignore[override]
        self.calls.append((kind, title, risk))
        return GuardDecision(False, risk, "Test: abgelehnt")


@pytest.fixture
def rec_ctx(make_ctx):
    guard = RecordingGuard()
    return guard, make_ctx(guard)


# --------------------------------------------------------------------------
# F7 — see_screen nimmt den GANZEN Bildschirm auf und kann das Bild an ein
#      Modell schicken. Ohne Guard lief das auch im readonly-Modus.
# --------------------------------------------------------------------------

def test_screenshot_consults_guard(rec_ctx):
    """F7: Der Guard muss VOR der Aufnahme gefragt werden."""
    guard, ctx = rec_ctx
    result = ScreenshotTool().run({}, ctx)
    assert guard.calls, "see_screen fragte den Guard überhaupt nicht"
    assert not result.ok, "see_screen lief trotz Ablehnung"


def test_screenshot_is_dangerous(rec_ctx):
    """F7: Bildschirminhalt kann Passwörter zeigen — nicht bloß MODERATE."""
    guard, ctx = rec_ctx
    ScreenshotTool().run({}, ctx)
    assert guard.calls[0][2] == Risk.DANGEROUS, guard.calls


# --------------------------------------------------------------------------
# F8 — clipboard liest Geheimnisse (Passwort-Manager) und schreibt Systemzustand.
# --------------------------------------------------------------------------

def test_clipboard_get_consults_guard(rec_ctx):
    """F8: Lesen der Zwischenablage ist ein Exfiltrationspfad."""
    guard, ctx = rec_ctx
    result = ClipboardTool().run({"action": "get"}, ctx)
    assert guard.calls, "clipboard get fragte den Guard nicht"
    assert not result.ok


def test_clipboard_set_consults_guard(rec_ctx):
    """F8: Schreiben verändert Systemzustand — im readonly-Modus unzulässig."""
    guard, ctx = rec_ctx
    result = ClipboardTool().run({"action": "set", "text": "x"}, ctx)
    assert guard.calls, "clipboard set fragte den Guard nicht"
    assert not result.ok


def test_clipboard_get_is_dangerous(rec_ctx):
    """F8: Lesen ist der riskantere der beiden Wege."""
    guard, ctx = rec_ctx
    ClipboardTool().run({"action": "get"}, ctx)
    assert guard.calls[0][2] == Risk.DANGEROUS, guard.calls


# --------------------------------------------------------------------------
# Geringeres Gewicht, gleiche Lücke: notify und adjust_volume.
# Argumente bewusst so gewählt, dass auch der ungefixte Pfad nichts verändert.
# --------------------------------------------------------------------------

def test_notify_consults_guard(rec_ctx):
    guard, ctx = rec_ctx
    NotifyTool().run({"message": ""}, ctx)
    assert guard.calls, "notify fragte den Guard nicht"


def test_volume_consults_guard(rec_ctx):
    guard, ctx = rec_ctx
    VolumeTool().run({"action": "unbekannt"}, ctx)
    assert guard.calls, "adjust_volume fragte den Guard nicht"


# --------------------------------------------------------------------------
# F9 — press_key war MODERATE, type_text DANGEROUS. `xdotool key` kann aber
#      Zeichen senden, also war press_key die laxere Tür zur selben Wirkung.
# --------------------------------------------------------------------------

def test_press_key_matches_type_text_severity(rec_ctx):
    """F9: Gleiche Wirkung, gleicher Risikograd."""
    guard, ctx = rec_ctx
    KeyPressTool().run({"keys": "a"}, ctx)
    key_risk = guard.calls[-1][2] if guard.calls else None

    guard2 = RecordingGuard()
    ctx2 = ctx.__class__(cfg=ctx.cfg, guard=guard2, workdir=ctx.workdir)
    TypeTextTool().run({"text": "a"}, ctx2)
    type_risk = guard2.calls[-1][2] if guard2.calls else None

    assert key_risk == type_risk, f"press_key={key_risk}, type_text={type_risk}"


def test_press_key_refused_in_readonly(rec_ctx):
    guard, ctx = rec_ctx
    assert not KeyPressTool().run({"keys": "ctrl+w"}, ctx).ok
