"""F3: python_exec darf die Shell-Klassifikation nicht umgehen.

Verhaltensbasiert, ohne Mocks: der Bestätigungs-Callback lehnt ab, und das
Werkzeug muss die Ausführung verweigern. Der ausgeführte Code ist harmlos —
wenn der Test fehlschlägt, hat er nur `print` aufgerufen.
"""
from __future__ import annotations

from minerva.safety.guard import Risk
from minerva.tools.python_exec import PythonEvalTool


def test_python_exec_refused_when_user_declines(make_guard, make_ctx):
    """F3: Beliebige Codeausführung braucht in guarded eine Zustimmung."""
    guard = make_guard(mode="guarded", confirm=False)
    ctx = make_ctx(guard)
    result = PythonEvalTool().run({"code": "print('harmlos')"}, ctx)
    assert not result.ok, "python_exec lief trotz Ablehnung durch den Nutzer"
    assert "abgelehnt" in result.content.lower()


def test_python_exec_runs_when_user_approves(make_guard, make_ctx):
    """Kein Kollateralschaden: mit Zustimmung läuft es."""
    guard = make_guard(mode="guarded", confirm=True)
    ctx = make_ctx(guard)
    result = PythonEvalTool().run({"code": "print('hallo')"}, ctx)
    assert result.ok, result.content
    assert "hallo" in result.content


def test_python_exec_blocked_in_readonly(make_guard, make_ctx):
    """readonly erlaubt nur Lesendes — Codeausführung gehört nicht dazu."""
    guard = make_guard(mode="readonly")
    ctx = make_ctx(guard)
    result = PythonEvalTool().run({"code": "print(1)"}, ctx)
    assert not result.ok


def test_python_exec_parity_with_shell(make_guard, make_ctx):
    """F3-Kern: gleiche Wirkung darf nicht unterschiedlich bewertet werden.

    Ein Shell-Befehl, der abgelehnt wird, darf über python_exec nicht doch
    durchlaufen.
    """
    guard = make_guard(mode="guarded", confirm=False)
    ctx = make_ctx(guard)

    shell_risk, _ = guard.classify_shell("rm -rf ~/wichtig")
    shell_allowed = guard.review("shell", "t", "x", shell_risk).allowed

    py = PythonEvalTool().run(
        {"code": "import os; print('würde os.system aufrufen')"}, ctx
    )
    assert shell_allowed is False
    assert py.ok is False, "Shell blockiert, python_exec nicht — Umgehung offen"


def test_empty_code_rejected(make_guard, make_ctx):
    """Randfall: leerer Code wird vor dem Guard abgefangen."""
    guard = make_guard(mode="yolo")
    ctx = make_ctx(guard)
    assert not PythonEvalTool().run({"code": "   "}, ctx).ok
