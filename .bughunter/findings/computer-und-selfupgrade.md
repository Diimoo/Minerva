# Findings: `minerva/tools/computer.py`, `minerva/tools/selfupgrade.py`

Review + Fix Mode: 2026-07-25 · **alle 4 Funde behoben**
Tests: `tests/test_computer_guard.py` (10), `tests/test_selfupgrade_rollback.py` (3)
Vor den Fixes waren 10 davon rot.

---

## F7 — HOCH: `see_screen` fragte den Guard überhaupt nicht

`resolved: yes` · Test: `test_screenshot_consults_guard`, `test_screenshot_is_dangerous`

**Defekt:** `ScreenshotTool.run()` rief `_capture_screenshot()` als erste
Anweisung auf — ohne jede Guard-Abfrage. Damit nahm das Werkzeug den gesamten
Bildschirm auf, schrieb eine PNG nach `~/.minerva/screenshots/` und schickte das
Bild bei gesetztem `question` an ein Vision-Modell. Auch im `readonly`-Modus,
und ohne eine Zeile im Audit-Log.

**Reproduktion (vorher):** `RecordingGuard` (lehnt alles ab) → `guard.calls`
blieb leer, `result.ok` war `True`. Der Screenshot entstand trotzdem.

**Auswirkung:** Die größte ungesicherte Datenfläche im Projekt. Ein Bildschirm
zeigt Passwort-Manager, Chats, Banking. Kein Gate, keine Spur.

**Fix:** `Risk.DANGEROUS`-Review **vor** der Aufnahme.

---

## F8 — HOCH: `clipboard` fragte den Guard überhaupt nicht

`resolved: yes` · Test: `test_clipboard_get_consults_guard`,
`test_clipboard_set_consults_guard`, `test_clipboard_get_is_dangerous`

**Defekt:** `ClipboardTool.run()` las (`get`) und schrieb (`set`) die
Zwischenablage ohne Guard. `set` verändert Systemzustand und lief damit auch im
`readonly`-Modus; `get` gibt zurück, was gerade in der Zwischenablage liegt —
bei Passwort-Managern also Geheimnisse.

**Fix:** Review vor der Verzweigung. `get` → `DANGEROUS` (Exfiltrationspfad),
`set` → `MODERATE` (Zustandsänderung).

**Ebenfalls ungeprüft, geringeres Gewicht, mit behoben:**
`notify` → `SAFE` (harmlos, aber protokolliert), `adjust_volume` → `MODERATE`.
Die Guard-Abdeckung von `computer.py` hat damit **0 Lücken** (8 von 8 Werkzeugen).

---

## F9 — MITTEL: `press_key` war die laxere Tür zur Wirkung von `type_text`

`resolved: yes` · Test: `test_press_key_matches_type_text_severity`

**Defekt:** `type_text` meldete `DANGEROUS`, `press_key` nur `MODERATE` — und
`MODERATE` läuft im `guarded`-Modus ohne Rückfrage. `xdotool key a b c` sendet
aber Zeichen, und Tastenkombinationen lösen beliebige UI-Aktionen aus
(`ctrl+w`, `Return` auf einem Bestätigungsdialog). Gleiche Wirkungsklasse,
unterschiedliches Gate — dasselbe Muster wie F3 bei `python_exec`.

**Fix:** `press_key` → `Risk.DANGEROUS`.

---

## F10 — HOCH: fehlgeschlagener Rollback wurde als erfolgreicher gemeldet

`resolved: yes` · Test: `test_restore_reports_failure`,
`test_restore_reports_success`, `test_restore_does_not_raise`

**Defekt:**

```python
def _restore(backup, dst) -> None:
    try:
        _sync_into(backup, dst)
    except Exception:
        pass          # <- jeder Fehler verschwindet
```

Die Aufrufer meldeten unabhängig davon `"Nach Übernahme defekt — automatisch
zurückgerollt."`. Auf dem sicherheitskritischsten Pfad des Projekts — Minerva
hat gerade ihren eigenen Quellcode überschrieben und der Selbsttest ist
fehlgeschlagen — konnte diese Meldung also falsch sein. Der Nutzer hätte einen
defekten Projektordner und die Zusage, es sei alles zurückgerollt.

**Fix:** `_restore` gibt `bool` zurück und protokolliert den Fehler. Beide
Aufrufstellen werten den Rückgabewert aus; im Fehlerfall liefert
`_rollback_failed()` eine Meldung, die auf den Backup-Pfad zeigt und den
`rsync`-Befehl zum Zurückspielen von Hand nennt. `_restore` wirft weiterhin
nicht — der Aufrufer steckt schon im Fehlerfall, und eine zweite Ausnahme würde
die Meldung ganz verhindern.

---

## Geprüft und widerlegt (kein Fund)

**Das `--selftest`-Gate existiert und funktioniert.** Hypothese war, dass
`self_upgrade` nie durchlaufen kann, weil das Validierungs-Gate fehlt.
Widerlegt: `__main__.py:192` definiert das Flag, und der Lauf bestätigt
`SELFTEST OK: 24 Werkzeuge, alle Module importierbar` (exit 0).

---

## Verifikation nach den Fixes

- `pytest` → **104 passed** (92 vorher + 12 neu)
- `python -m minerva --selftest` → OK, 24 Werkzeuge
- Guard-Abdeckung `computer.py` → 0 Lücken
- `yolo`-Verhalten unverändert: `see_screen`, `clipboard get`, `press_key`
  bleiben erlaubt. Der Unterschied liegt im Audit-Log und in `guarded`/`readonly`.

## Nebenwirkung, bewusst

Autorisierung läuft jetzt **vor** der Fähigkeitsprüfung (`_have(...)`). Ein
abgelehntes Werkzeug meldet „Abgelehnt" statt „xdotool nicht installiert".
Das ist die richtige Reihenfolge — ob etwas erlaubt ist, hängt nicht davon ab,
ob das Binary zufällig vorhanden ist — und macht die Tests unabhängig von der
installierten Werkzeugkette.
