"""Sprache-zu-Text: faster-whisper + Mikrofon-Aufnahme via PipeWire (pw-record).

MicListener nimmt bei aktivem Zuhören Audio auf, segmentiert Äußerungen per
energiebasiertem VAD (Voice Activity Detection) und transkribiert jede Äußerung
mit faster-whisper auf der GPU. Fertige Transkripte gehen per Callback an den
Orchestrator.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("minerva.voice.stt")

RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = RATE * FRAME_MS // 1000       # 480
FRAME_BYTES = FRAME_SAMPLES * 2               # s16 mono


class STTEngine:
    def __init__(self, model: str = "small", device: str = "cuda",
                 compute_type: str = "float16", language: Optional[str] = None) -> None:
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self._model = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel

            try:
                self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute_type)
                log.info("STT geladen: %s (%s/%s)", self.model_name, self.device, self.compute_type)
            except Exception as exc:  # noqa: BLE001
                log.warning("STT auf %s fehlgeschlagen (%s) — fallback CPU/int8.", self.device, exc)
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")

    def transcribe_pcm(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        self._ensure()
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        try:
            segments, _info = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            return "".join(s.text for s in segments).strip()
        except Exception as exc:  # noqa: BLE001
            log.error("Transkription fehlgeschlagen: %s", exc)
            return ""

    def warmup(self) -> None:
        """Lädt das Modell im Voraus (verhindert Latenz beim ersten Sprechen)."""
        try:
            self._ensure()
            self.transcribe_pcm(np.zeros(RATE // 2, dtype=np.int16).tobytes())
        except Exception:  # noqa: BLE001
            pass


class MicListener(threading.Thread):
    """Kontinuierliches Zuhören mit VAD; nur aktiv, wenn set_active(True)."""

    def __init__(
        self,
        stt: STTEngine,
        on_utterance: Callable[[str], None],
        on_level: Optional[Callable[[float], None]] = None,
        on_state: Optional[Callable[[str], None]] = None,
        mic_device: Optional[str] = None,
        silence_ms: int = 900,
        energy_threshold: float = 0.010,
        min_speech_ms: int = 350,
        max_utterance_ms: int = 30000,
    ) -> None:
        super().__init__(name="minerva-mic", daemon=True)
        self.stt = stt
        self.on_utterance = on_utterance
        self.on_level = on_level or (lambda lvl: None)
        self.on_state = on_state or (lambda st: None)
        self.mic_device = mic_device
        self.silence_ms = silence_ms
        self.energy_threshold = energy_threshold
        self.min_speech_ms = min_speech_ms
        self.max_utterance_ms = max_utterance_ms

        self.running = True
        self.active = False
        self._proc: Optional[subprocess.Popen] = None

    # -- Steuerung ---------------------------------------------------------
    def set_active(self, value: bool) -> None:
        self.active = value
        self.on_state("listening" if value else "idle")

    def toggle(self) -> bool:
        self.set_active(not self.active)
        return self.active

    def shutdown(self) -> None:
        self.running = False
        self.active = False
        self._kill_proc()

    # -- Aufnahme ----------------------------------------------------------
    def _spawn_recorder(self) -> Optional[subprocess.Popen]:
        if shutil.which("pw-record"):
            cmd = ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "--raw"]
            if self.mic_device:
                cmd += ["--target", str(self.mic_device)]
            cmd += ["-"]
        elif shutil.which("arecord"):
            cmd = ["arecord", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw", "-q"]
            if self.mic_device:
                cmd = ["arecord", "-D", str(self.mic_device)] + cmd[1:]
        else:
            log.error("Kein Aufnahme-Tool (pw-record/arecord) gefunden.")
            return None
        try:
            return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except Exception as exc:  # noqa: BLE001
            log.error("Recorder-Start fehlgeschlagen: %s", exc)
            return None

    def _kill_proc(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1)
            except Exception:  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def _read_frame(self) -> Optional[bytes]:
        assert self._proc and self._proc.stdout
        buf = bytearray()
        while len(buf) < FRAME_BYTES:
            chunk = self._proc.stdout.read(FRAME_BYTES - len(buf))
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def run(self) -> None:
        while self.running:
            if not self.active:
                if self._proc:
                    self._kill_proc()
                time.sleep(0.05)
                continue
            if not self._proc:
                self._proc = self._spawn_recorder()
                if not self._proc:
                    self.active = False
                    time.sleep(0.5)
                    continue
            self._capture_session()

    def _capture_session(self) -> None:
        preroll: list[bytes] = []            # kurze Vorlaufpuffer gegen abgeschnittene Wörter
        preroll_max = 10                     # ~300 ms
        speech: list[bytes] = []
        triggered = False
        speech_ms = 0
        silence_ms = 0

        while self.running and self.active and self._proc:
            frame = self._read_frame()
            if frame is None:
                break
            samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(samples ** 2))) if samples.size else 0.0
            self.on_level(min(1.0, rms * 6))

            if rms >= self.energy_threshold:
                if not triggered:
                    triggered = True
                    speech.extend(preroll)  # Vorlauf mit übernehmen
                    self.on_state("hearing")
                speech.append(frame)
                speech_ms += FRAME_MS
                silence_ms = 0
            else:
                preroll.append(frame)
                if len(preroll) > preroll_max:
                    preroll.pop(0)
                if triggered:
                    speech.append(frame)
                    silence_ms += FRAME_MS
                    if silence_ms >= self.silence_ms and speech_ms >= self.min_speech_ms:
                        self._finalize(speech)
                        speech, preroll = [], []
                        triggered = False
                        speech_ms = silence_ms = 0

            if triggered and speech_ms >= self.max_utterance_ms:
                self._finalize(speech)
                speech, preroll = [], []
                triggered = False
                speech_ms = silence_ms = 0

    def _finalize(self, frames: list[bytes]) -> None:
        pcm = b"".join(frames)
        self.on_state("transcribing")
        text = self.stt.transcribe_pcm(pcm)
        self.on_state("listening" if self.active else "idle")
        text = (text or "").strip()
        if text:
            self.on_utterance(text)
