"""MINERVA-App: verdrahtet Gehirn, Werkzeuge, Sprache und Oberfläche.

Fäden (Threads):
  * GUI-Thread    — Qt, Orb/HUD/Tray, TTS-Auslösung.
  * Mic-Thread    — Aufnahme + VAD + STT (MicListener).
  * Worker-Thread — pro Anfrage ein Thread, der den Orchestrator laufen lässt.
Alle Rückmeldungen an die GUI laufen über thread-sichere Qt-Signale (Bridge).
"""
from __future__ import annotations

import difflib
import logging
import os
import re
import subprocess
import sys
import threading
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMenu

from . import PROJECT_ROOT
from .config import Config, resolve_brain_backend
from .core.orchestrator import Orchestrator
from .core.state import AgentState
from .brain.factory import build_backend
from .memory import ConversationMemory
from .memories import MemoryStore
from .rag_service import RagService
from .safety import Guard
from .skills import SkillManager
from .tools.registry import ToolContext, build_default_registry
from .ui.confirm import ConfirmController
from .ui.hud import HudWindow
from .ui.orb import AnimatedOrb
from .ui.tray import Tray

log = logging.getLogger("minerva.app")


class Bridge(QObject):
    """Thread-sichere Signale Worker/Mic -> GUI."""

    token = pyqtSignal(str)
    event = pyqtSignal(str, str)          # (typ, text)
    state = pyqtSignal(object)            # AgentState
    transcript = pyqtSignal(str)          # erkannte Nutzer-Sprache
    level = pyqtSignal(float)             # Mikrofonpegel
    assistant_done = pyqtSignal(str)      # finale Antwort
    busy_changed = pyqtSignal(bool)
    speak_done = pyqtSignal()             # TTS-Wiedergabe beendet
    request_restart = pyqtSignal(float)   # Selbst-Neustart nach n Sekunden


class JarvisApp:
    def __init__(self, cfg: Config, qapp: QApplication, no_voice: bool = False) -> None:
        self.cfg = cfg
        self.qapp = qapp
        self.no_voice = no_voice
        self._busy = False
        self._busy_lock = threading.Lock()
        self._was_listening = False
        # Antwortfenster: Zeitraum nach einer Antwort, in dem eine Äußerung
        # auch OHNE Weckwort akzeptiert wird (Sprachbeginn zählt).
        self._followup_until = 0.0
        self._speech_started_at = 0.0

        # -- Kern-Komponenten ---------------------------------------------
        self.guard = Guard(
            mode=cfg.get("safety.mode", "guarded"),
            hard_denylist=cfg.get("safety.hard_denylist", []),
            confirm_timeout_s=cfg.get("safety.confirm_timeout_s", 60),
            audit=cfg.get("safety.audit", True),
        )
        self.backend = build_backend(cfg)
        self.registry = build_default_registry(cfg)
        self.skill_manager = SkillManager()
        self.rag = RagService(cfg) if cfg.get("tools.rag_enabled", True) else None
        self.memories = (
            MemoryStore(cfg.get("memories.dir", "~/.minerva/memories"),
                        max_inject_chars=cfg.get("memories.max_inject_chars", 4000))
            if cfg.get("memories.enabled", True) else None
        )

        workdir = cfg.get("tools.workdir")
        self.tool_ctx = ToolContext(
            cfg=cfg,
            guard=self.guard,
            workdir=__import__("pathlib").Path(workdir).expanduser() if workdir else __import__("pathlib").Path.home(),
        )
        self.tool_ctx.backend = self.backend
        self.tool_ctx.skill_manager = self.skill_manager
        self.tool_ctx.registry = self.registry
        self.tool_ctx.rag = self.rag
        self.tool_ctx.memories = self.memories
        self.tool_ctx.app = self

        # generierte Skills laden
        for msg in self.skill_manager.load_all(self.registry):
            log.info("Skill: %s", msg)

        self.memory = ConversationMemory()
        self.orchestrator = Orchestrator(cfg, self.backend, self.registry, self.tool_ctx, self.memory)

        # -- UI ------------------------------------------------------------
        self.bridge = Bridge()
        self.confirm = ConfirmController(timeout_s=cfg.get("safety.confirm_timeout_s", 60))
        self.guard.set_confirm_fn(self.confirm.confirm)

        self.name = cfg.get("persona.name", "Minerva")
        self.orb = AnimatedOrb(size=cfg.get("ui.orb_size", 140))
        self.hud = HudWindow()
        self._followup_timer = QTimer()
        self._followup_timer.setInterval(250)
        self._followup_timer.timeout.connect(self._tick_followup)
        self.hud.set_title(self.name)
        self.tray = Tray(self)
        self.tray.setToolTip(self.name)

        # Tool-Ereignisse in die GUI leiten
        self.tool_ctx.emit = lambda et, txt: self.bridge.event.emit(et, txt)

        # -- Sprache -------------------------------------------------------
        self.tts = None
        self.stt = None
        self.mic = None
        if cfg.get("voice.enabled", True) and not no_voice:
            self._init_voice()

        self._wire_signals()
        self._position_windows()
        self._start_hotkeys()

        # Begrüßung
        backend_name = resolve_brain_backend(cfg)
        self.hud.append_system(f"MINERVA bereit · Gehirn: {backend_name} · Modus: {self.guard.mode}")
        self.hud.set_status(f"{backend_name} · {self.guard.mode}")
        self.orb.set_state("idle")

        # Optional: gesprochene Begrüßung (kurz, nach dem UI-Aufbau).
        if self.tts and cfg.get("ui.spoken_greeting", True) and cfg.get("voice.tts_enabled", True):
            name = cfg.get("persona.name", "MINERVA")
            greeting = f"{name} ist online und bereit, Sir."
            QTimer.singleShot(1600, lambda: self._speak_greeting(greeting))

    def _speak_greeting(self, greeting: str) -> None:
        # Mikrofon während der Begrüßung pausieren — sonst würde das im Text
        # enthaltene Weckwort "Minerva" das Zuhören selbst auslösen.
        was_active = bool(self.mic and self.mic.active)
        if self.mic:
            self.mic.set_active(False)
        self.orb.set_state("speaking")  # GUI-Thread (via QTimer)
        self.hud.show_agent_state("speaking")

        def _resume() -> None:
            # läuft im Sprech-Thread: Zustand nur über thread-sichere Signale ändern
            if self.mic and (was_active or self.cfg.get("voice.require_wake_word", False)):
                self.mic.set_active(True)  # emittiert 'listening' via Signal
            else:
                self.bridge.state.emit(AgentState.IDLE)

        threading.Thread(target=self.tts.speak, args=(greeting,),
                         kwargs={"on_done": _resume}, daemon=True).start()

    # ------------------------------------------------------------------ Voice
    def _init_voice(self) -> None:
        from .voice import MicListener, STTEngine, build_tts

        self.tts = build_tts(self.cfg)
        self.stt = STTEngine(
            model=self.cfg.get("voice.stt_model", "small"),
            device=self.cfg.get("voice.stt_device", "cuda"),
            compute_type=self.cfg.get("voice.stt_compute_type", "float16"),
            language=self.cfg.get("voice.stt_language"),
            hotwords=self.cfg.get("voice.stt_hotwords") or self.cfg.get("persona.name", "Minerva"),
        )
        self.mic = MicListener(
            stt=self.stt,
            on_utterance=lambda t: self.bridge.transcript.emit(t),
            on_level=lambda lvl: self.bridge.level.emit(lvl),
            on_state=lambda st: self.bridge.state.emit(_state_from_str(st)),
            mic_device=self.cfg.get("voice.mic_device"),
            silence_ms=self.cfg.get("voice.vad_silence_ms", 900),
            energy_threshold=self.cfg.get("voice.vad_energy_threshold", 0.010),
            min_speech_ms=self.cfg.get("voice.vad_min_speech_ms", 350),
            max_utterance_ms=self.cfg.get("voice.vad_max_utterance_ms", 30000),
        )
        self.mic.start()
        # Ressourcenschonend: KEIN eager Warmup. STT lädt erst bei der ersten
        # erkannten Sprache (VAD ist reines numpy, kostet ~nichts im Leerlauf).
        # Im Weckwort-Modus lauscht das Mikrofon dauerhaft (nur VAD), reagiert
        # aber erst auf "Minerva".
        if self.cfg.get("voice.require_wake_word", False):
            self.mic.set_active(True)

    # ------------------------------------------------------------------ Wiring
    def _wire_signals(self) -> None:
        self.bridge.token.connect(self.hud.append_assistant_token)
        self.bridge.event.connect(self._on_event)
        self.bridge.state.connect(self._on_state)
        self.bridge.transcript.connect(self._on_transcript)
        self.bridge.level.connect(self.orb.set_level)
        self.bridge.assistant_done.connect(self._on_assistant_done)
        self.bridge.busy_changed.connect(self._on_busy_changed)
        self.bridge.speak_done.connect(self._finish_turn)
        self.bridge.request_restart.connect(self._do_restart)

        self.orb.clicked.connect(self.toggle_listen)
        self.orb.double_clicked.connect(self.toggle_hud)
        self.orb.context_requested.connect(self._show_orb_menu)

        self.hud.submit_text.connect(self._on_user_text)
        self.hud.toggle_listen.connect(self.toggle_listen)
        self.hud.stop_all.connect(self.stop_speaking)
        self.hud.clear_chat.connect(self.memory.clear)

    def _position_windows(self) -> None:
        screen = self.qapp.primaryScreen().availableGeometry()
        # Orb unten rechts
        ox = screen.right() - self.orb.width() - 40
        oy = screen.bottom() - self.orb.height() - 60
        self.orb.move(ox, oy)
        # HUD daneben
        self.hud.move(screen.right() - self.hud.width() - 40, screen.bottom() - self.hud.height() - 220)
        if self.cfg.get("ui.show_on_start", True):
            self.orb.show()
        if not self.cfg.get("ui.start_hidden_console", False):
            self.hud.show()

    # ------------------------------------------------------------------ Slots
    def _on_event(self, etype: str, text: str) -> None:
        if etype == "tool_call":
            self.hud.append_tool(text)
        elif etype == "tool_result":
            self.hud.append_tool(text)
        elif etype in ("error",):
            self.hud.append_error(text)
        elif etype in ("warn", "info"):
            self.hud.append_system(text)
        else:
            self.hud.append_system(f"{etype}: {text}")

    def _on_state(self, state: AgentState) -> None:
        key = state.value if isinstance(state, AgentState) else str(state)
        if state == AgentState.HEARING:
            # Sprachbeginn merken — entscheidet, ob eine Äußerung noch ins
            # Antwortfenster fällt (Transkription kommt erst deutlich später).
            self._speech_started_at = time.time()
        if state != AgentState.LISTENING and self._followup_timer.isActive():
            self._followup_timer.stop()
        self.orb.set_state(key)
        self.hud.show_agent_state(key)

    def _on_transcript(self, text: str) -> None:
        # Sprache erkannt -> wie Nutzereingabe behandeln.
        if self._busy:
            return
        if self.cfg.get("voice.require_wake_word", False):
            stripped = self._apply_wake_word(text)
            in_followup = 0.0 < self._speech_started_at <= self._followup_until
            if stripped is not None:
                text = stripped
            elif not in_followup:
                self.hud.append_system(f"(kein Weckwort — gehört: „{text}“)")
                return
            self._followup_until = 0.0
        self.hud.append_user(text)
        self._start_handling(text)

    def _apply_wake_word(self, text: str):
        """Gibt den Text ohne Weckwort zurück, oder None, wenn keins vorkam."""
        return strip_wake_word(
            text,
            self.cfg.get("voice.wake_words", []),
            min_ratio=self.cfg.get("voice.wake_word_min_similarity", 0.75),
        )

    def _on_user_text(self, text: str) -> None:
        self.hud.append_user(text)
        self._start_handling(text)

    def _on_assistant_done(self, text: str) -> None:
        self.hud.end_assistant()
        if not text or not (self.tts and self.cfg.get("voice.tts_enabled", True)):
            self._finish_turn()
            return
        # Sprechen; _finish_turn wird über bridge.speak_done ausgelöst, sobald die
        # Wiedergabe endet (bei Piper exakt, bei Higgs per Schätzung). Ein
        # Sicherheits-Timer verhindert Hängenbleiben.
        self.orb.set_state("speaking")
        self.hud.show_agent_state("speaking")
        spoken = _for_speech(text)
        self._finish_guard = False

        def _done() -> None:
            self.bridge.speak_done.emit()

        threading.Thread(target=self.tts.speak, args=(spoken,), kwargs={"on_done": _done},
                         name="minerva-speak", daemon=True).start()
        # Sicherheitsnetz: spätestens nach Schätzdauer + Puffer weiter.
        QTimer.singleShot(int((self.tts.estimate_duration_s(spoken) + 8) * 1000), self._finish_turn)

    def _finish_turn(self) -> None:
        # Idempotent: wird von speak_done UND vom Sicherheits-Timer gerufen.
        if not getattr(self, "_finish_pending", False):
            return
        self._finish_pending = False
        self._set_busy(False)
        # Im Weckwort-Modus IMMER wieder lauschen; sonst nur, wenn vorher aktiv.
        resume = bool(self.mic) and (self._was_listening or self.cfg.get("voice.require_wake_word", False))
        if resume:
            self.mic.set_active(True)
            self.orb.set_state("listening")
            self.hud.show_agent_state("listening")
            # Antwortfenster öffnen: kurz ohne Weckwort weiterreden können.
            window = float(self.cfg.get("voice.followup_window_s", 5.0))
            if window > 0 and self.cfg.get("voice.require_wake_word", False):
                self._followup_until = time.time() + window
                self._followup_timer.start()
                self._tick_followup()
        else:
            self.orb.set_state("idle")
            self.hud.show_agent_state("idle")

    def _tick_followup(self) -> None:
        remaining = self._followup_until - time.time()
        if self._busy or remaining <= 0 or not (self.mic and self.mic.active):
            self._followup_timer.stop()
            if not self._busy:
                self.hud.show_agent_state(
                    "listening" if (self.mic and self.mic.active) else "idle")
            return
        self.hud.show_followup(remaining)

    def _on_busy_changed(self, busy: bool) -> None:
        self.hud.input.setEnabled(not busy)

    # ------------------------------------------------------------------ Handling
    def _start_handling(self, text: str) -> None:
        if self._busy:
            self.hud.append_system("(beschäftigt — bitte warten)")
            return
        self._set_busy(True)
        self._finish_pending = True
        # Während Denken/Sprechen Mikro pausieren (kein TTS-Feedback).
        self._was_listening = bool(self.mic and self.mic.active)
        if self.mic:
            self.mic.set_active(False)
        self.hud.start_assistant()

        def _work() -> None:
            try:
                final = self.orchestrator.handle(
                    text,
                    on_token=lambda t: self.bridge.token.emit(t),
                    emit=lambda et, txt: self.bridge.event.emit(et, txt),
                    on_state=lambda s: self.bridge.state.emit(s),
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("Handling-Fehler")
                self.bridge.event.emit("error", str(exc))
                final = f"Es gab einen Fehler: {exc}"
            self.bridge.assistant_done.emit(final)

        threading.Thread(target=_work, name="minerva-worker", daemon=True).start()

    def _set_busy(self, value: bool) -> None:
        with self._busy_lock:
            self._busy = value
        self.bridge.busy_changed.emit(value)

    # ------------------------------------------------------------------ Aktionen (app_ctx)
    def toggle_listen(self) -> None:
        if not self.mic:
            self.hud.append_system("Sprache ist deaktiviert (--no-voice oder voice.enabled=false).")
            return
        active = self.mic.toggle()
        self.hud.set_listening(active)
        self.orb.set_state("listening" if active else "idle")
        self.hud.append_system("Zuhören aktiviert." if active else "Zuhören deaktiviert.")

    def toggle_hud(self) -> None:
        self.hud.setVisible(not self.hud.isVisible())
        if self.hud.isVisible():
            self.hud.raise_()
            self.hud.activateWindow()

    def toggle_orb(self) -> None:
        self.orb.setVisible(not self.orb.isVisible())

    def set_safety_mode(self, mode: str) -> None:
        self.guard.mode = mode
        self.cfg.set("safety.mode", mode)
        self.hud.append_system(f"Sicherheitsmodus: {mode}")
        self.hud.set_status(f"{self.backend.name} · {mode}")

    def stop_speaking(self) -> None:
        if self.tts:
            self.tts.stop()
        self.hud.append_system("Gestoppt.")
        self._finish_turn()

    def _show_orb_menu(self, global_pos) -> None:
        menu: QMenu = self.tray.contextMenu()
        menu.exec(global_pos)

    def quit(self) -> None:
        try:
            if self.mic:
                self.mic.shutdown()
            if self.tts:
                self.tts.stop()
            if self.rag:
                self.rag.close()
        finally:
            self.qapp.quit()

    # ------------------------------------------------------------------ Neustart (Selbst-Upgrade)
    def schedule_restart(self, delay_s: float = 2.0) -> None:
        """Thread-sicher: aus dem Worker-Thread aufrufbar."""
        self.bridge.request_restart.emit(float(delay_s))

    def _do_restart(self, delay_s: float) -> None:
        self.hud.append_system("Neustart wird vorbereitet …")
        QTimer.singleShot(int(delay_s * 1000), self._restart_now)

    def _restart_now(self) -> None:
        run_sh = PROJECT_ROOT / "run.sh"
        env = dict(os.environ)
        env.setdefault("QT_QPA_PLATFORM", "xcb")
        try:
            if run_sh.exists():
                subprocess.Popen(["bash", str(run_sh)], cwd=str(PROJECT_ROOT),
                                 start_new_session=True, env=env)
            else:
                subprocess.Popen([sys.executable, "-m", "minerva"], cwd=str(PROJECT_ROOT),
                                 start_new_session=True, env=env)
        except Exception as exc:  # noqa: BLE001
            log.error("Neustart fehlgeschlagen: %s", exc)
            self.hud.append_error(f"Neustart fehlgeschlagen: {exc}")
            return
        # aktuelle Instanz beenden
        if self.mic:
            self.mic.shutdown()
        if self.tts:
            self.tts.stop()
        self.qapp.quit()

    # ------------------------------------------------------------------ Hotkeys
    def _start_hotkeys(self) -> None:
        hk = self.cfg.get("hotkeys", {}) or {}
        mapping = {}
        if hk.get("toggle_listen"):
            mapping[hk["toggle_listen"]] = lambda: self.bridge.event.emit("_hotkey", "toggle_listen")
        if hk.get("push_to_talk"):
            mapping[hk["push_to_talk"]] = lambda: self.bridge.event.emit("_hotkey", "toggle_listen")
        if hk.get("stop_speaking"):
            mapping[hk["stop_speaking"]] = lambda: self.bridge.event.emit("_hotkey", "stop")
        if not mapping:
            return
        # Hotkey-Ereignisse laufen über die Bridge in den GUI-Thread.
        self.bridge.event.connect(self._on_hotkey_event)
        try:
            from pynput import keyboard

            self._hotkeys = keyboard.GlobalHotKeys(mapping)
            self._hotkeys.daemon = True
            self._hotkeys.start()
            log.info("Globale Hotkeys aktiv: %s", list(mapping))
        except Exception as exc:  # noqa: BLE001
            log.warning("Globale Hotkeys nicht verfügbar (evtl. Wayland): %s", exc)

    def _on_hotkey_event(self, etype: str, text: str) -> None:
        if etype != "_hotkey":
            return
        if text == "toggle_listen":
            self.toggle_listen()
        elif text == "stop":
            self.stop_speaking()


# ---------------------------------------------------------------------- Helfer
def strip_wake_word(text: str, wake_words: list[str], min_ratio: float = 0.75):
    """Entfernt ein Weckwort am Äußerungsanfang; None, wenn keins vorkam.

    Whisper transkribiert Eigennamen nicht immer exakt („Minerwa", „Mineva",
    „Minärva"). Deshalb werden Wortfenster der ersten drei Wörter fuzzy
    (difflib) gegen die Weckwörter verglichen, statt exakt zu matchen.
    """
    matches = list(re.finditer(r"[\w]+", text, flags=re.UNICODE))
    words = [m.group(0).lower() for m in matches]
    # Längere Weckwörter zuerst („hey minerva" vor „minerva").
    for wake in sorted({w.lower().strip() for w in wake_words if w.strip()},
                       key=lambda w: -len(w.split())):
        n = len(wake.split())
        for start in range(min(3, len(words) - n + 1)):
            cand = " ".join(words[start:start + n])
            if cand == wake or difflib.SequenceMatcher(None, cand, wake).ratio() >= min_ratio:
                rest = text[matches[start + n - 1].end():].lstrip(" ,.:;!?-–—").strip()
                return rest or text.strip()
    return None


def _state_from_str(name: str) -> AgentState:
    try:
        return AgentState(name)
    except ValueError:
        return AgentState.IDLE


def _for_speech(text: str) -> str:
    """Bereinigt Text fürs Vorlesen (entfernt Markdown-Reste, kürzt sehr lang)."""
    import re

    t = re.sub(r"```.*?```", " (Codeblock ausgelassen) ", text, flags=re.DOTALL)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"[*_#>]+", "", t)
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:1200]
