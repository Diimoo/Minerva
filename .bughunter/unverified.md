# Unverifizierte Verdachtsmomente

**Keine Funde.** Hier stehen Dinge, die falsch aussehen, sich aber nicht
reproduzieren ließen. Sie werden erst zu Funden, wenn eine Reproduktion vorliegt.

---

## U1 — `Config.save()` schreibt nicht atomar

`minerva/config.py:227-231` öffnet mit `open(target, "w")` und trunkiert damit
sofort. Bricht der Prozess zwischen Truncate und `yaml.safe_dump` ab, bliebe eine
leere `config.yaml` zurück.

**Warum kein Fund:** Es gibt genau **einen** Aufrufer (`config.py:260`), und der
steht hinter `if not cfg_path.exists()`. Eine existierende Konfiguration wird
also nie überschrieben — der Wipe-Pfad ist im aktuellen Code nicht erreichbar.

**Latenz-Hinweis:** Sobald irgendwo ein Laufzeit-`cfg.save()` hinzukommt (etwa
ein Settings-Dialog), wird U1 sofort zu einem echten Fund. Dann `os.replace()`
über eine Temp-Datei im selben Verzeichnis verwenden.

Gleiches Muster, gleiche Bewertung: `minerva/memories.py:49,55`
(`path.write_text`) — dort ist der Aufrufpfad live, der Verlust betrifft aber
eine einzelne Notiz, nicht die Gesamtkonfiguration.

---

## U2 — `_busy` könnte dauerhaft hängen bleiben

`minerva/app.py:346-366`: `_set_busy(False)` passiert ausschließlich in
`_finish_turn`, ausgelöst von `speak_done` oder dem QTimer-Sicherheitsnetz
(app.py:302). Wirft der Signal-Handler, bevor der Sprech-Pfad den Timer startet,
wird `_busy` nie zurückgesetzt — und `_start_handling` verwirft dann jede weitere
Eingabe mit „(beschäftigt — bitte warten)".

**Warum kein Fund:** Nicht reproduziert. Der Pfad braucht einen laufenden
Qt-Eventloop und einen erzwungenen Fehler im Handler; das ohne GUI-Harness zu
inszenieren war im Rahmen dieses Durchlaufs nicht möglich. Reine Codelektüre
reicht als Beleg nicht.

---

## U3 — zwei parallele Ollama-Daemons

`PID 2476 /usr/local/bin/ollama serve` und `PID 3338 /bin/ollama serve` laufen
gleichzeitig; nur einer kann Port 11434 halten.

**Warum kein Fund:** Kein Code-Defekt in Minerva, sondern Zustand der
Arbeitsumgebung. Verursacht das gemeldete Weckwort-Symptom nicht — die
Modell-Liste war über 11434 erreichbar und `qwen3.5:9b` vorhanden.
