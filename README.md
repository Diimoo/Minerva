# M.I.N.E.R.V.A — lokaler, sprachgesteuerter Desktop-Assistent

Minerva ist ein nativer Desktop-Agent (kein Web, kein Browser) mit einer ruhigen,
autoritären weiblichen Persona. Sie **hört auf ihr Weckwort „Minerva"**, spricht mit
weiblicher Stimme, bedient deinen Computer und die CLI, ruft Claude Code auf, führt ein
persistentes Gedächtnis über dich und **verbessert ihren eigenen Code** — Kopie anlegen,
umbauen, testen, übernehmen, selbst neu starten.

Läuft **vollständig lokal** über Ollama (kein API-Key nötig) — optional über die
Anthropic-API (z. B. Opus) oder über **Claude Code**, also über das Pro/Max-Abo statt über
abgerechnete API-Token. Ein schwebender „Arc-Reactor"-Orb zeigt den Zustand, eine
HUD-Konsole den Dialog und die Werkzeug-Aktivität.

```
        ◈  ← schwebender Orb (Klick = zuhören, Doppelklick = Konsole, Rechtsklick = Menü)
   ┌─────────────────────────────┐
   │ ◈ MINERVA          spreche… │
   │ Sie ▸ Minerva, öffne Code … │
   │ ⚙ open_app({"target":…})    │
   │ MINERVA ▸ Erledigt, Sir.    │
   │ ◉ spreche…                  │  ← animierte Zustandszeile
   │ [ Nachricht … ] [Senden]    │
   └─────────────────────────────┘
```

## Fähigkeiten

- **Sprache:** Weckwort „Minerva" (immer lauschbereit, aber ressourcenschonend), STT per
  faster-whisper (GPU), Antworten per **Piper** (leichtgewichtige weibliche dt. Stimme).
  Das Weckwort wird **fuzzy** erkannt („Minerwa", „Mineva" zählen mit), und nach jeder
  Antwort bleibt ein **Antwortfenster** offen, in dem man ohne Weckwort weiterreden kann.
- **Umschaltbares Gehirn:** Ollama lokal (`qwen3.5:9b`), Anthropic-API (Opus) **oder**
  **Claude Code** über das Pro/Max-Abo (`claude_code`, kein API-Key nötig).
- **Computer bedienen:** Shell/CLI, Dateien, Maus/Tastatur, Screenshots + Bildschirm
  „sehen" (Vision-Modell), Apps/Dateien öffnen, Lautstärke, Zwischenablage, Notifications.
- **Claude Code aufrufen:** delegiert große Programmieraufgaben an die `claude` CLI.
- **Persönliches Gedächtnis:** schreibt Fakten/Präferenzen über dich als Notizen in
  `~/.minerva/memories/` und **kennt dich** beim nächsten Start wieder.
- **Semantisches Langzeitgedächtnis (RAG, optional):** eigenes, **persistentes** Qdrant
  (Docker, Port 6335) über das separate `rag-module`-Projekt — ohne es läuft Minerva
  einfach ohne RAG.
- **Selbstverbesserung:** schreibt neue Skills (`create_skill`) und kann ihren **eigenen
  Code** sicher upgraden (`self_upgrade`): Kopie → Claude Code → Selbsttest → Übernahme mit
  Backup/Rollback → Neustart.
- **Sicherheitsnetz:** jedes Werkzeug läuft durch den Guard — inklusive Screenshot,
  Zwischenablage, Tastatur/Maus und Python-Ausführung; katastrophale Shell-Befehle werden
  **strukturell** erkannt (an der Wirkung, nicht an der Schreibweise).

## Installation

Voraussetzungen (Linux; entwickelt unter GNOME/Wayland):

- **Python ≥ 3.11** und ein Mikrofon (PipeWire, `pw-record`)
- **[Ollama](https://ollama.com)** für das lokale LLM — oder ein `ANTHROPIC_API_KEY`,
  oder eine eingerichtete `claude` CLI (Abo-Login)
- Optional: `xdotool` (Maus/Tastatur-Steuerung), Docker (persistentes Qdrant für RAG),
  GNOME-Erweiterung *AppIndicator* (Tray-Icon)

```bash
git clone https://github.com/Diimoo/Minerva.git
cd Minerva
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Lokales Modell für das Standard-Backend:
ollama pull qwen3.5:9b

# Weibliche deutsche Piper-Stimme nach ~/.minerva/voices laden:
python -m piper.download_voices --download-dir ~/.minerva/voices de_DE-kerstin-low
```

Optionale Extras (einzeln oder kombiniert, z. B. `pip install -e ".[rag,dev]"`):

| Extra | Wofür |
|---|---|
| `rag` | semantisches Langzeitgedächtnis (`qdrant-client`, `fastembed`) |
| `claude-code` | Gehirn über das Claude-Abo (`claude-agent-sdk`, braucht die `claude` CLI) |
| `dev` | Testsuite (`pytest`) |

## Schnellstart

```bash
./run.sh                 # native GUI (Orb + Konsole + Tray)
./run.sh --cli           # Text-REPL im Terminal (ohne Qt/Audio) — ideal zum Testen
./run.sh --no-voice      # GUI ohne Sprache
./run.sh --selftest      # Validierungslauf (dasselbe Gate wie beim Selbst-Upgrade)
./run.sh --backend anthropic --model claude-opus-4-8   # Opus (ANTHROPIC_API_KEY nötig)
./run.sh --backend claude_code   # Gehirn über das Claude-Abo (keine API-Kosten)
```

`--model` wirkt immer auf das gewählte Backend: bei `ollama` auf `brain.model`, bei
`anthropic` auf `brain.anthropic_model`, bei `claude_code` auf `brain.claude_code_model`.
Ohne `--model` bleibt es beim jeweiligen Default (bei `claude_code` also bei der Vorgabe
der `claude` CLI).

Autostart beim Login: `./scripts/install-autostart.sh` einrichten, mit
`./scripts/install-autostart.sh --remove` wieder entfernen.

### Bedienung

| Aktion | So geht's |
|---|---|
| Ansprechen | **„Minerva, …"** sagen (Weckwort, fuzzy erkannt) |
| Nachhaken | direkt nach ihrer Antwort **ohne Weckwort** weiterreden (5-s-Fenster mit Countdown) |
| Zuhören an/aus | Klick auf den Orb / Tray |
| Konsole zeigen/verstecken | Doppelklick auf den Orb / Tray |
| Menü (Modus, Stop, Beenden) | **Rechtsklick auf den Orb** |
| Tippen statt sprechen | Text in die Konsole |

Die Zustandszeile unter dem Verlauf zeigt animiert, was gerade passiert: `bereit`,
`höre zu…`, `nehme auf…`, `lade Sprachmodell…`, `verarbeite Sprache…`, `denke nach…`,
`spreche…`. Wird kein Weckwort erkannt, protokolliert die Konsole, was sie *stattdessen*
verstanden hat — das macht Fehlerkennungen debuggbar.

Beispiele:
> „Minerva, zeig mir, was auf dem Bildschirm ist." · „Minerva, merke dir, dass ich
> morgens schwarzen Kaffee trinke." · „Minerva, lass Claude Code die Tests reparieren."
> · „Minerva, verbessere dich selbst: füge ein Werkzeug für Kalender-Einträge hinzu."

## Ressourcenverhalten (wichtig)

Minerva darf dauerhaft laufen, **ohne Ressourcen zu fressen, bis du sie ansprichst**:

- Im Leerlauf läuft nur die günstige Weckwort-Erkennung (Mikrofon + energie­basierter
  VAD in numpy). **Kein GPU-Modell** ist geladen.
- Das **STT-Modell** (Whisper) lädt erst, wenn du tatsächlich sprichst — der erste Ladevorgang
  ist spürbar und wird als eigener Zustand `lade Sprachmodell…` angezeigt.
- Die **Stimme** (Piper) ist CPU-leicht und sofort da — kein 4B-Modell im VRAM.
- Das **LLM** (Ollama) lädt beim ersten Auftrag und gibt VRAM im Leerlauf wieder frei.
- Das **Gedächtnis** (Qdrant) ist ein schlanker Docker-Container; Embedding-Modelle laden
  erst bei der ersten Gedächtnis-Nutzung.

## Architektur

```
minerva/
├─ __main__.py     Einstieg: GUI | --cli | --selftest
├─ app.py          Verdrahtung: Threads, Signale, Voice, Hotkeys, Neustart
├─ config.py       config.yaml + .env + Env-Overrides (MINERVA_*)
├─ brain/          umschaltbares LLM: base · ollama · anthropic · claude_code · factory
├─ core/           Zustandsmaschine + Orchestrator (Tool-Calling-Loop)
├─ tools/          shell · files · computer · claude_code · rag · memories ·
│                  python · web · selfimprove · selfupgrade
│                  (25 Werkzeuge, davon 24 per Default aktiv — `web_fetch`
│                   schaltet `tools.web_enabled: true` frei)
├─ voice/          stt (faster-whisper + pw-record + VAD) · tts (Piper | Higgs)
├─ memories.py     persönliches Notizgedächtnis (Markdown, in den Prompt injiziert)
├─ rag_service.py  Brücke zum ~/Proj/rag-module + Qdrant-Autostart (Docker)
├─ skills/         dynamischer Skill-Loader (Hot-Reload)
├─ safety/         Guard: Befehlsklassifikation · Bestätigung · Audit-Log
└─ ui/             orb · hud · tray · confirm · theme (PyQt6)

tests/            Regressionssuite für Guard, Config und Selbst-Upgrade
.bughunter/       Inventar und Befunde des Code-Audits
```

### Gehirn über Claude Code

Das Backend `claude_code` nutzt das **Agent SDK** unter dem Abo-Login der `claude` CLI —
ohne `ANTHROPIC_API_KEY`. Entscheidend ist, dass Minervas Werkzeuge dabei **nicht** vom SDK
ausgeführt werden: die eingebauten SDK-Werkzeuge sind abgeschaltet, Minervas eigene
`ToolSpec`s werden als In-Process-MCP-Server nur *deklariert*, und ein PreToolUse-Hook
reicht jeden Werkzeugwunsch an den Orchestrator zurück. Ausgeführt wird ausschließlich über
die Registry — und damit durch den Guard.

`claude_code` ist bewusst **kein** Teil von `backend: auto`: das Abo hat Rate-Limits, die
ein dauerhaft lauschender Assistent sonst beiläufig aufbraucht. Ollama bleibt das
Alltags-Gehirn, `claude_code` wird gezielt gewählt.

## Konfiguration

Beim ersten Start entsteht `~/.minerva/config.yaml`. Wichtige Schalter:

```yaml
persona: { name: Minerva, language: de }
brain:
  backend: auto            # auto | ollama | anthropic | claude_code
  model: qwen3.5:9b        # lokal (zuverlässiges Tool-Calling)
  anthropic_model: claude-opus-4-8
  claude_code_model: null  # null = Vorgabe der `claude` CLI übernehmen
  claude_code_effort: null # null | low | medium | high | xhigh | max
  claude_code_timeout: 300 # Sekunden pro Denk-Schritt
voice:
  require_wake_word: true  # nur nach „Minerva" reagieren
  wake_words: [minerva, hey minerva, hallo minerva, okay minerva]
  wake_word_min_similarity: 0.75  # Fuzzy-Toleranz für verhörte Weckwörter
  followup_window_s: 5.0   # danach ohne Weckwort weiterreden (0 = aus)
  stt_language: de         # feste Sprache; null = Auto-Erkennung
  stt_hotwords: null       # null = persona.name, also „Minerva"
  tts_backend: piper       # piper (leicht, weiblich) | higgs (HQ, GPU)
  piper_model: ~/.minerva/voices/de_DE-kerstin-low.onnx
  piper_length_scale: 1.2  # >1 = getragener/autoritärer
safety:
  mode: yolo               # yolo | guarded | readonly
rag:
  qdrant_url: http://127.0.0.1:6335   # eigenes persistentes Qdrant (Docker)
memories:
  enabled: true
  dir: ~/.minerva/memories
```

Overrides per Env: `MINERVA_BRAIN_MODEL=llama3.1:latest`, siehe [.env.example](.env.example).

Ein syntaktisch defektes `config.yaml` bricht den Start **mit Pfadangabe** ab, statt still
auf die Defaults zurückzufallen — sonst würde ein Tippfehler unbemerkt z. B. `safety.mode`
zurücksetzen.

## Sicherheit

Modus `yolo` (Default) lässt Minerva ohne Rückfragen handeln — außer bei katastrophalen
Aktionen. Alternativen: `guarded` (Bestätigungsdialog bei gefährlichen Aktionen) und
`readonly` (nur Lesen). Alle sicherheitsrelevanten Aktionen landen in
`~/.minerva/audit.log`.

Der Guard klassifiziert Shell-Befehle **strukturell**, also anhand geparster Token je
Verkettungssegment statt anhand der Zeichenkette. Dadurch greift die Sperre unabhängig von
der Schreibweise — `rm -fr /`, `rm --recursive --force /` und `cd / && rm -rf .` werden
gleichermaßen erkannt, ebenso `mkfs*`, Partitionierer, `dd` auf Blockgeräte, Fork-Bomben
und Power-Kommandos. Umgekehrt sperrt die Sperrliste nicht mehr versehentlich Harmloses
(`rm -rf /tmp/scratch` enthält zwar `rm -rf /`, ist aber kein Sperrlisten-Treffer).

Auch außerhalb der Shell gilt: `python` läuft als **DANGEROUS** (beliebiger Python-Code
kann alles, was die Shell kann), und Screenshot, Zwischenablage, Tastendruck, Lautstärke
und Benachrichtigungen laufen ebenfalls durch den Guard — Bildschirminhalt und
Zwischenablage sind Exfiltrationspfade, keine harmlosen Leseoperationen.

## Selbst-Upgrade (eigener Code)

`self_upgrade` verbessert Minervas eigenen Quellcode **sicher**:

1. Kopie des Projekts in `~/.minerva/upgrades/<ts>`.
2. Claude Code setzt die Verbesserung in der Kopie um.
3. **Selbsttest-Gate** (`python -m minerva --selftest`) muss bestehen.
4. Backup des aktuellen Stands → Übernahme → erneuter Selbsttest.
5. Bei Fehler: **automatischer Rollback** aus dem Backup.
6. Erfolg: Minerva startet sich selbst neu.

## Tests

```bash
pip install -e ".[dev]"
pytest                      # 104 Tests: Guard-Klassifikation, Config, Rollback
python -m minerva --selftest  # Importe + Verdrahtung (Gate des Selbst-Upgrades)
```

Der Schwerpunkt liegt auf dem Guard: Umgehungsversuche der Sperrliste, verändernde
Argumente bei nominell lesenden Befehlen (`find -delete`, `ip … set`), Programme aus
untrusted Pfaden und der Rollback-Pfad des Selbst-Upgrades.

## Bekannte Einschränkungen (GNOME/Wayland)

- **Tray-Icon** braucht die GNOME-Erweiterung *AppIndicator*
  (`sudo apt install gnome-shell-extension-appindicator`, dann in „Erweiterungen"
  aktivieren). Ohne sie erreichst du alle Funktionen über das **Orb-Rechtsklickmenü**.
- **Maus/Tastatur** (xdotool) wirken auf XWayland-Fenster & Terminal; native
  Wayland-Fenster nehmen injizierte Eingaben teils nicht an. Screenshots laufen über das
  GNOME-Shell-Portal.
- Minerva läuft unter **XWayland** (`QT_QPA_PLATFORM=xcb`) für ein zuverlässiges Overlay.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
