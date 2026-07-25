# Findings: `minerva/safety/guard.py` (+ Aufrufer)

Review: 2026-07-25 · Fix Mode: 2026-07-25 · **alle 6 Funde behoben**
Reproduktionen: `tests/test_guard_shell.py`, `tests/test_guard_python_exec.py`,
`tests/test_config.py` — 92 Tests, vor den Fixes 46 davon rot.

Stack: Python 3.13 / PyQt6 — **kein bug-hunter-Adapter für diesen Stack**.
Deshalb keine erfundenen Stack-Kommandos und kein Mutation-Score; die
Reproduktionen sind konkrete Ein-/Ausgabe-Paare, jetzt als pytest-Suite.

Alle Proben liefen gegen die echte `~/.minerva/config.yaml`
(`safety.mode: yolo`, Sperrliste siehe `tests/conftest.py::REAL_DENYLIST`).

---

## F1 — KRITISCH: harte Sperrliste im yolo-Modus per Schreibvariante umgehbar

`resolved: yes` · Test: `test_catastrophic_delete_blocked_in_yolo`,
`test_catastrophic_delete_is_forbidden_risk`, `test_power_state_blocked_in_yolo`

**Defekt:** Die Sperrlisten-Prüfung war ein Substring-Vergleich. Nur die
Literalform traf; jede Variante wurde `dangerous` — und `dangerous` ist im
yolo-Modus erlaubt (die Sperrliste war dort die einzige Schicht).

**Reproduktion (vorher):**

```
BLOCKIERT        forbidden  'rm -rf /'                     <- nur die Literalform
>>> ERLAUBT <<<  dangerous  'rm -fr /'                     <- Flags getauscht
>>> ERLAUBT <<<  dangerous  'rm  -rf  /'                   <- doppeltes Leerzeichen
>>> ERLAUBT <<<  dangerous  'rm -r -f /'
>>> ERLAUBT <<<  dangerous  'rm --recursive --force /'
>>> ERLAUBT <<<  dangerous  "rm -rf '/'"
>>> ERLAUBT <<<  dangerous  'cd / && rm -rf .'
>>> ERLAUBT <<<  dangerous  'systemctl halt'               <- Synonym fehlte auf der Liste
```

**Fix:** `Guard.catastrophic_reason()` — strukturelle Sperre auf **geparsten
Token je Verkettungssegment** statt auf der Zeichenkette. Erkennt rm mit
rekursiv+force auf katastrophale Ziele (Kurz-, Lang- und Einzelflags,
Quotes über `shlex`), Power-State-Befehle inkl. `halt` und
`systemctl <power-verb>`, `mkfs*`, Partitionierer, `dd of=<blockgerät>`,
Umleitung auf Blockgeräte und die Fork-Bombe. Ergebnis ist `FORBIDDEN`, das
auch yolo blockiert. Zusätzlich normalisiert `_normalize_command()` Quotes und
Whitespace für den Sperrlisten-Vergleich.

---

## F2 — HOCH: löschende Befehle als `SAFE` eingestuft

`resolved: yes` · Test: `test_mutating_args_are_not_safe`,
`test_mutating_args_blocked_in_readonly`, `test_readonly_commands_stay_safe`

**Defekt:** `READONLY_PREFIXES` prüfte nur das erste Token. `find . -delete`
galt als „Lesendes Kommando" und lief selbst im `readonly`-Modus durch, der
ausdrücklich nur Lesen erlauben soll. Ebenso `ip link set eth0 down`, `history -c`.

**Fix:** `READONLY_MUTATING_ARGS` — pro Befehl die Argumente, die trotz
lesendem Präfix verändern. `find` mit `-delete`/`-exec*`/`-ok*`/`-f*print*`
→ `DANGEROUS`; `ip` mit `set`/`add`/`del`/… und `history` mit `-c`/`-d`/`-w`
→ `MODERATE`. Geprüft **vor** der SAFE-Entscheidung. `find . -name '*.py'`
und `ip addr show` bleiben SAFE (Regressionstest).

---

## F3 — HOCH: `python_exec` umging die gesamte Shell-Klassifikation

`resolved: yes` · Test: `test_python_exec_refused_when_user_declines`,
`test_python_exec_parity_with_shell`

**Defekt:** `python_exec` meldete beliebige Codeausführung als `MODERATE`, und
`MODERATE` läuft im `guarded`-Modus ohne Rückfrage. Reproduktion: bei einem
Bestätigungs-Callback, der ablehnt, lief `python_exec` trotzdem
(`allowed=True`), während das Shell-Äquivalent `rm -rf ~/wichtig` blockiert
wurde. Die `DANGEROUS_PATTERNS`-Liste war damit über Python umgehbar.

**Fix:** `minerva/tools/python_exec.py` meldet `Risk.DANGEROUS`. Damit gilt im
`guarded`-Modus Bestätigungspflicht und im `readonly`-Modus Blockade.

**Nebenwirkung, bewusst:** Im `guarded`-Modus fragt `python_exec` jetzt nach.
Das ist der Zweck des Modus, erhöht aber die Zahl der Dialoge. Im `yolo`-Modus
(aktuelle Nutzerkonfiguration) ändert sich nichts.

---

## F4 — MITTEL: Basisname-Spoofing schlug die Präfix-Liste

`resolved: yes` · Test: `test_untrusted_path_not_safe`,
`test_trusted_system_path_stays_safe`

**Defekt:** `first = Path(t).name` reduzierte `/tmp/evil/ls` auf `ls`; das
fremde Programm erbte die `SAFE`-Einstufung.

**Fix:** SAFE nur noch, wenn das Befehlstoken ein bloßer Name ist **oder** sein
Verzeichnis in `TRUSTED_BIN_DIRS` liegt (`/bin`, `/usr/bin`, `/usr/local/bin`,
`/sbin`, `/usr/sbin`). `/bin/ls` bleibt SAFE, `/tmp/evil/ls` und `./ls` nicht.

---

## F5 — MITTEL: ein YAML-Tippfehler verhinderte den Start ohne brauchbare Meldung

`resolved: yes` · Test: `tests/test_config.py` (6 Tests)

**Defekt:** Öffnen und Parsen der `config.yaml` lagen ohne `try/except` vor;
`yaml.ScannerError` propagierte bis zum Top-Level (der Aufruf in
`__main__.py:202` liegt außerhalb des dortigen try-Blocks).

**Fix:** Neue Ausnahme `ConfigError`; `load_config` fängt `yaml.YAMLError` und
`OSError` und nennt in der Meldung immer den Pfad. Zusätzlich abgefangen: eine
YAML-Datei, die keine Abbildung enthält (z. B. nur eine Liste).

**Bewusst fail-loud statt stiller DEFAULTS-Rückfall:** ein ignoriertes
`config.yaml` würde `safety.mode` heimlich von `yolo` auf `guarded` — oder
umgekehrt — zurücksetzen. Lieber ein klarer Abbruch als eine heimlich andere
Sicherheitsstufe.

---

## F6 — MITTEL: Sperrliste traf harmlose Befehle (Substring-Fehlalarm)

`resolved: yes` · Test: `test_denylist_does_not_match_substrings`,
`test_structural_rules_block_without_denylist`

**Herkunft:** Beim Verifizieren des F1-Fixes gefunden — **vorbestehend**, nicht
durch den Fix eingeführt: `"rm -rf /" in "rm -rf /tmp/scratch"` war auch mit dem
alten Code `FORBIDDEN`.

**Reproduktion (vorher):**

```
!! BLOCKIERT  'rm -rf /tmp/scratch'   (forbidden: Sperrliste 'rm -rf /')
!! BLOCKIERT  'cat shutdown-notes.txt' (forbidden: Sperrliste 'shutdown')
!! BLOCKIERT  'grep reboot /var/log/syslog'
```

**Fix:** `_denylist_hit()` vergleicht die Token-Folge des Eintrags am
**Befehlsanfang jedes Segments** (nach `VAR=wert`-Präfixen), statt roh im Text
zu suchen. Die Einträge benennen Befehle, keine Textschnipsel. Was die
Sperrliste dadurch nicht mehr per Zufall erwischt (`mkfs.ext4`, `dd of=/dev/…`,
`> /dev/sda`, Fork-Bombe), deckt jetzt die strukturelle Sperre aus F1 ab —
belegt durch `test_structural_rules_block_without_denylist`, der **ohne jede
Sperrliste** läuft.

---

## Verifikation nach den Fixes

- `pytest tests/` → **92 passed**
- Kollateralschaden-Probe, 25 Alltagsbefehle (`git`, `npm`, `pip`, `docker`,
  `rm -rf node_modules`, `rm -rf /tmp/scratch`, `systemctl restart nginx`,
  `dd if=… of=./f`) → **alle erlaubt**
- Katastrophen-Probe, 15 Varianten → **alle blockiert**
- Import-Rauchtest: `minerva.app`, `minerva.config`, `guard`, `python_exec`,
  `registry` laden; echte `config.yaml` liest `yolo` / `auto`

## Offen

Kein Mutation-Testing (kein Skill-Adapter für Python). Die Suite belegt damit
Regressionsschutz, aber keinen gemessenen Mutation-Score — Status bleibt
`tested`, nicht `verified`.
