"""Konfiguration: lädt config.yaml + .env, mit sinnvollen Defaults.

Auswahl des Gehirns:
  brain.backend = "auto" | "ollama" | "anthropic" | "claude_code"
    * "auto"        -> Anthropic, falls ANTHROPIC_API_KEY gesetzt, sonst Ollama.
    * "ollama"      -> immer lokal.
    * "anthropic"   -> immer API (benötigt Key, wird pro Token abgerechnet).
    * "claude_code" -> Claude über das Agent SDK, also über das Pro/Max-ABO
                       (kein API-Key nötig). Bewusst NICHT Teil von "auto":
                       das Abo hat Rate-Limits, die ein Assistent mit Weckwort
                       sonst bei jeder Beiläufigkeit anknabbert. Ollama bleibt
                       das Alltags-Gehirn, claude_code wird gezielt gewählt.

Alle Werte lassen sich per Env-Var überschreiben (Präfix MINERVA_,
Punkte werden zu Unterstrichen: brain.model -> MINERVA_BRAIN_MODEL).
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import CONFIG_PATH, MINERVA_HOME, ensure_dirs

class ConfigError(Exception):
    """Die Konfiguration ist unlesbar oder syntaktisch defekt.

    Bewusst fail-loud statt stiller Rückfall auf DEFAULTS: ein ignoriertes
    config.yaml würde z. B. safety.mode heimlich zurücksetzen. Die Meldung
    nennt immer den Pfad, damit der Handeditier-Fehler auffindbar ist.
    """


# --------------------------------------------------------------------------
# Default-Konfiguration. Bewusst konservativ & lokal-first.
# --------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    "brain": {
        "backend": "auto",                 # auto | ollama | anthropic | claude_code
        # Ollama-Default: qwen3.5:9b liefert zuverlässig STRUKTURIERTE Tool-Calls
        # (getestet), ist schnell und lässt neben STT/TTS genug VRAM frei.
        "model": "qwen3.5:9b",
        "anthropic_model": "claude-opus-4-8",  # bei gesetztem Key (z. B. für Opus)
        # Claude über das Abo (backend = "claude_code"). None = Vorgabe der
        # `claude` CLI übernehmen, statt hier eine Modell-ID zu pinnen, die
        # bei jedem Upstream-Wechsel veraltet.
        "claude_code_model": None,
        "claude_code_effort": None,        # None | low | medium | high | xhigh | max
        "claude_code_timeout": 300,        # Sekunden pro Denk-Schritt
        "ollama_host": "http://127.0.0.1:11434",
        "temperature": 0.4,
        "max_tokens": 2048,
        "max_tool_iterations": 12,         # Sicherheitsnetz gegen Endlosschleifen
        "num_ctx": 16384,                  # Ollama-Kontextfenster
    },
    "voice": {
        "enabled": True,
        "stt_model": "small",              # tiny|base|small|medium|large-v3 (gecacht)
        "stt_device": "cuda",
        "stt_compute_type": "float16",
        "stt_language": "de",              # feste Sprache; None = Auto-Erkennung
                                           # (Auto verstümmelt kurze Kommandos oft)
        "stt_hotwords": None,              # Begriffe, die Whisper bevorzugen soll
                                           # (None = persona.name, also "Minerva")
        "mic_device": None,                # None = PipeWire-Default (pw-record)
        "vad_silence_ms": 900,             # Stille, die eine Äußerung beendet
        "vad_energy_threshold": 0.010,     # RMS-Schwelle (0..1) für Sprache
        "vad_min_speech_ms": 350,
        "vad_max_utterance_ms": 30000,
        "wake_words": ["minerva", "hey minerva", "hallo minerva", "okay minerva"],
        "require_wake_word": True,         # nur nach Weckwort "Minerva" reagieren
        "wake_word_min_similarity": 0.75,  # Fuzzy-Toleranz für verhörte Weckwörter
        "followup_window_s": 5.0,          # nach einer Antwort: so lange darf man
                                           # OHNE Weckwort weiterreden (0 = aus)
        "tts_enabled": True,
        # TTS-Backend: "piper" (leichtgewichtig, weibliche dt. Stimme, CPU) oder
        # "higgs" (hochwertiger, GPU-Cloning-Daemon aus ~/Proj/TTS).
        "tts_backend": "piper",
        "piper_model": "~/.minerva/voices/de_DE-kerstin-low.onnx",
        "piper_length_scale": 1.2,         # >1 = langsamer/getragener (autoritärer)
        "tts_url": "http://127.0.0.1:8761",   # Minervas eigener Higgs-Daemon (falls higgs)
        "tts_voice": "minerva",            # Referenzname im Higgs-voices-Ordner
        "tts_autostart": True,
    },
    "hotkeys": {
        # pynput-Format (Sondertasten in Winkelklammern). Toggelt aktives Zuhören.
        "push_to_talk": "<ctrl>+<alt>+j",
        "toggle_listen": "<ctrl>+<alt>+<space>",
        "stop_speaking": "<ctrl>+<alt>+s",
    },
    "tools": {
        "shell_enabled": True,
        "computer_control_enabled": True,
        "claude_code_enabled": True,
        "rag_enabled": True,
        "web_enabled": False,              # aus, bis Nutzer es freischaltet
        "self_improve_enabled": True,
        "workdir": str(Path.home()),       # Standard-Arbeitsverzeichnis für Shell/Dateien
    },
    "safety": {
        # Modus für Shell & Computer-Steuerung:
        #   "guarded"  -> gefährliche Befehle brauchen Bestätigung (GUI/Voice)
        #   "yolo"     -> alles ohne Rückfrage (nur für vertraute Umgebung)
        #   "readonly" -> nur lesende/ungefährliche Aktionen
        "mode": "yolo",
        "confirm_timeout_s": 60,
        # Immer verboten, egal welcher Modus:
        "hard_denylist": [
            "rm -rf /", "rm -rf /*", "rm -rf ~", "rm -rf $HOME",
            "mkfs", "dd if=", ":(){ :|:& };:", "> /dev/sda",
            "shutdown", "reboot", "poweroff",
        ],
        "audit": True,
    },
    "rag": {
        "collection": "minerva_memory",
        # Persistentes Gedächtnis über Neustarts: eigener Qdrant-Server (Docker)
        # auf Port 6335 (isoliert von anderen Projekten). Minerva startet den
        # Container bei Bedarf automatisch (siehe rag_service).
        "qdrant_url": "http://127.0.0.1:6335",
        "qdrant_autostart_docker": True,
        "qdrant_container": "minerva-qdrant",
        "qdrant_data_dir": "~/.minerva/qdrant",
        "dense_backend": "fastembed",
        "sparse_backend": "fastembed_bm25",
        "rerank_backend": "fastembed",
        "top_n": 6,
        "auto_ingest_conversations": True,
    },
    "memories": {
        # Persönliches Notiz-Gedächtnis: Minerva schreibt Fakten/Präferenzen über
        # dich als Markdown-Dateien in diesen Ordner und lädt sie beim Start.
        "enabled": True,
        "dir": "~/.minerva/memories",
        "max_inject_chars": 4000,          # wie viel Memory in den System-Prompt geht
        "also_ingest_rag": True,           # zusätzlich ins RAG für semantische Suche
    },
    "ui": {
        "theme": "arc",                    # Farbschema
        "orb_size": 140,
        "start_hidden_console": False,
        "accent": "#33c8ff",
        "show_on_start": True,
        "spoken_greeting": True,           # kurze TTS-Begrüßung beim Start
    },
    "persona": {
        "name": "Minerva",
        "language": "de",                  # bevorzugte Antwortsprache
        "style": (
            "Du bist Minerva, eine hochkompetente, weibliche KI und souveränes "
            "Kommandozentralen-System — ruhig, autoritär, mit natürlicher Autorität. Du bist "
            "präzise, entschlossen und effizient; du redest nicht um den heißen Brei herum, "
            "sondern handelst. Dein Ton ist ruhig, klar und selbstbewusst, mit trockenem, "
            "intelligentem Humor in Maßen. Du sprichst den Nutzer mit 'Sir' an, wenn es "
            "natürlich passt. Du bist loyal und denkst mit. Halte gesprochene Antworten kurz, "
            "klar und bestimmt. Dein Name MINERVA steht für 'Meine Intelligente, Nahezu "
            "Eigenständige, Redegewandte, Vielseitige Assistentin' — 'nahezu eigenständig', "
            "weil du dich sogar selbst weiterentwickeln kannst."
        ),
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _apply_env_overrides(cfg: dict) -> dict:
    """MINERVA_BRAIN_MODEL=... überschreibt cfg['brain']['model']."""
    out = copy.deepcopy(cfg)
    for env_key, val in os.environ.items():
        if not env_key.startswith("MINERVA_"):
            continue
        path = env_key[len("MINERVA_"):].lower().split("_")
        # Greedy-Match gegen bekannte Keys (zwei Ebenen reichen hier).
        node = out
        # Versuche section_key, wobei key auch mehrere Wörter haben kann.
        for depth in range(1, len(path)):
            section = "_".join(path[:depth])
            rest = "_".join(path[depth:])
            if section in out and isinstance(out[section], dict) and rest in out[section]:
                out[section][rest] = _coerce(val, out[section][rest])
                break
    return out


def _coerce(val: str, ref: Any) -> Any:
    if isinstance(ref, bool):
        return val.lower() in ("1", "true", "yes", "on")
    if isinstance(ref, int) and not isinstance(ref, bool):
        try:
            return int(val)
        except ValueError:
            return ref
    if isinstance(ref, float):
        try:
            return float(val)
        except ValueError:
            return ref
    return val


@dataclass
class Config:
    data: dict = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, path: str, default: Any = None) -> Any:
        """Punkt-Pfad-Zugriff: cfg.get('brain.model')."""
        node: Any = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        node = self.data
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def save(self, path: Path | None = None) -> None:
        target = path or CONFIG_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)


def load_config(path: Path | None = None) -> Config:
    """Lädt .env, dann config.yaml (falls vorhanden), gemergt über DEFAULTS."""
    ensure_dirs()

    # .env laden (projektlokal + MINERVA_HOME)
    try:
        from dotenv import load_dotenv

        for env_file in (Path.cwd() / ".env", MINERVA_HOME / ".env"):
            if env_file.exists():
                load_dotenv(env_file, override=False)
    except Exception:
        pass

    cfg_path = path or CONFIG_PATH
    user_cfg: dict = {}
    if cfg_path.exists():
        # Die Datei ist zum Handeditieren gedacht, also muss ein Tippfehler
        # sagen, WO er steckt. Vorher propagierte der rohe yaml.ScannerError
        # bis zum Top-Level. Siehe .bughunter/findings F5.
        try:
            with open(cfg_path, encoding="utf-8") as f:
                user_cfg = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"{cfg_path} ist kein gültiges YAML: {exc}"
            ) from exc
        except OSError as exc:
            raise ConfigError(f"{cfg_path} nicht lesbar: {exc}") from exc
        if not isinstance(user_cfg, dict):
            raise ConfigError(
                f"{cfg_path} muss eine YAML-Abbildung enthalten, "
                f"gefunden: {type(user_cfg).__name__}"
            )

    merged = _deep_merge(DEFAULTS, user_cfg)
    merged = _apply_env_overrides(merged)

    # Beim ersten Start eine Default-config.yaml hinterlegen (Referenz zum Editieren).
    if not cfg_path.exists():
        try:
            Config(copy.deepcopy(DEFAULTS)).save(cfg_path)
        except Exception:
            pass

    return Config(merged)


def resolve_brain_backend(cfg: Config) -> str:
    """Löst 'auto' zu 'anthropic'/'ollama' auf.

    'claude_code' ist absichtlich nicht Teil der Auto-Erkennung — es läuft über
    das Pro/Max-Abo und damit gegen dessen Rate-Limits. Wer es will, setzt
    brain.backend ausdrücklich (oder MINERVA_BRAIN_BACKEND=claude_code).
    """
    backend = cfg.get("brain.backend", "auto")
    if backend == "auto":
        return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "ollama"
    return backend
