# Inventar — M.I.N.E.R.V.A

Stack: Python 3.13 + PyQt6. **Die bug-hunter-Skill hat keinen Adapter für diesen
Stack** (nur Laravel+Vue). Deshalb keine erfundenen Stack-Kommandos, kein
Mutation-Testing-Score — Reproduktionen sind konkrete Ein-/Ausgabe-Paare.

Seit 2026-07-25 existiert eine Suite: `tests/` (92 Tests, `pytest tests/`).
`verified` bleibt trotzdem unerreichbar — es fehlt ein Mutation-Score, und die
Skill hat keinen Adapter für diesen Stack. Höchster ehrlicher Status: `tested`.

Status: `untouched` → `inspected` → `tested` → `verified`

## Kritischer Pfad (Reihenfolge dieses Durchlaufs)

| Einheit | Status | Funde | Tests |
|---|---|---|---|
| `safety/guard.py` | **tested** | F1 (kritisch), F2, F4, F6 — alle behoben | `test_guard_shell.py` (81) |
| `tools/python_exec.py` (Aufrufstelle) | **tested** | F3 — behoben | `test_guard_python_exec.py` (5) |
| `config.py` (load) | **tested** | F5 — behoben | `test_config.py` (6) |
| `tools/computer.py` | **tested** | F7, F8, F9 — alle behoben; Guard-Abdeckung 0 Lücken | `test_computer_guard.py` (10) |
| `tools/selfupgrade.py` | **tested** | F10 — behoben; `--selftest`-Gate geprüft, funktioniert | `test_selfupgrade_rollback.py` (3) |
| `tools/shell.py` | inspected | — (Guard korrekt verdrahtet) | indirekt |
| `app.py` (`_on_transcript`, `strip_wake_word`) | inspected | — (Weckwort-Gate arbeitet spezifikationsgemäß) | — |
| `core/orchestrator.py` | inspected | — | — |
| `brain/base.py`, `brain/factory.py` | inspected | — | — |
| `brain/claude_code_backend.py` | inspected | — (Defer-Mechanismus per Mutationsprobe belegt) | — |

`config.py::Config.save` bleibt bei `inspected` — siehe U1 in `unverified.md`,
der Wipe-Pfad ist im aktuellen Code nicht erreichbar und daher nicht testbar.

## Nicht angesehen

| Einheit | Zeilen | Grund |
|---|---|---|
| `tools/selfimprove.py` | 220 | Zeit; schreibt Code, der danach ausgeführt wird — höchstes verbleibendes Risiko |
| `ui/hud.py`, `ui/orb.py` | 249 + 194 | GUI, braucht Qt-Harness |
| `voice/stt.py`, `voice/tts.py` | 241 + 234 | Zeit |
| `rag_service.py` | 184 | Zeit |
| `tools/files.py`, `tools/memories.py`, `tools/rag.py`, `tools/web.py` | ~380 | Zeit |
| `skills/__init__.py` | 118 | Zeit |
| `memories.py` | 121 | nur Persistenzmuster gestreift (siehe U1) |
| `brain/ollama_backend.py`, `brain/anthropic_backend.py` | 259 | Zeit |

**Abdeckung: 10 von ~24 Einheiten auf `inspected` oder besser, 5 davon `tested`.**
Der Rest steht auf `untouched`. Kein „bug-free" — nur diese Verteilung.

Gesamt: **10 Funde** (F1–F10), alle behoben, 104 Tests grün.

## Empfohlene Reihenfolge für den nächsten Durchlauf

1. `tools/selfimprove.py` — schreibt neue Skills, die danach ausgeführt werden
2. `tools/files.py` — Pfad-Traversal beim Schreiben?
3. `app.py` `_busy`-Zustandsmaschine — braucht einen Qt-Harness (siehe U2)
4. `voice/stt.py` / `voice/tts.py` — Subprozess-Handhabung, Temp-Dateien
