"""Text-zu-Sprache — umschaltbar zwischen zwei Backends:

  * "piper" (Default für Minerva): leichtgewichtige, weibliche deutsche Stimme,
    läuft auf der CPU, keine 4B-Modelle, sofort einsatzbereit — ideal für einen
    dauerhaft laufenden, ressourcenschonenden Assistenten.
  * "higgs":  hochwertiger GPU-Cloning-Daemon aus ~/Proj/TTS. Minerva bekommt
    einen EIGENEN Daemon (anderer Port + eigene Referenzstimme), damit dein
    globaler Vorlese-Dienst (Super+Y) unberührt bleibt.

Beide Backends bieten dieselbe Schnittstelle: speak(text, on_done), stop(),
estimate_duration_s(text).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from .. import TTS_SERVER_DIR

log = logging.getLogger("minerva.voice.tts")

OnDone = Optional[Callable[[], None]]


def _play_cmd(path: str) -> Optional[list[str]]:
    for exe in ("pw-play", "paplay", "aplay", "ffplay"):
        if shutil.which(exe):
            if exe == "ffplay":
                return ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", path]
            return [exe, path]
    return None


class BaseTTS:
    def speak(self, text: str, on_done: OnDone = None) -> bool:
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def ensure_running(self) -> bool:
        return True

    @staticmethod
    def estimate_duration_s(text: str) -> float:
        words = max(1, len((text or "").split()))
        return min(45.0, max(1.2, words / 2.6))


# --------------------------------------------------------------------------
# Piper (leichtgewichtig, weiblich, CPU)
# --------------------------------------------------------------------------
class PiperTTS(BaseTTS):
    def __init__(self, model_path: str, length_scale: float = 1.0) -> None:
        self.model_path = os.path.expanduser(model_path)
        self.length_scale = length_scale
        self._play_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        if not os.path.exists(self.model_path):
            log.warning("Piper-Modell nicht gefunden: %s", self.model_path)

    def _synthesize(self, text: str) -> Optional[str]:
        out = tempfile.NamedTemporaryFile(prefix="minerva_", suffix=".wav", delete=False)
        out.close()
        cmd = [sys.executable, "-m", "piper", "-m", self.model_path, "-f", out.name]
        if abs(self.length_scale - 1.0) > 1e-3:
            cmd += ["--length-scale", str(self.length_scale)]
        try:
            proc = subprocess.run(cmd, input=text, text=True, capture_output=True, timeout=120)
            if proc.returncode != 0 or not os.path.getsize(out.name):
                log.error("Piper-Synthese fehlgeschlagen: %s", proc.stderr[-300:])
                return None
            return out.name
        except Exception as exc:  # noqa: BLE001
            log.error("Piper-Fehler: %s", exc)
            return None

    def speak(self, text: str, on_done: OnDone = None) -> bool:
        text = (text or "").strip()
        if not text:
            if on_done:
                on_done()
            return False
        self.stop()  # laufende Wiedergabe abbrechen (barge-in)
        wav = self._synthesize(text)
        if not wav:
            if on_done:
                on_done()
            return False
        play = _play_cmd(wav)
        if not play:
            log.warning("Kein Audio-Player gefunden.")
            if on_done:
                on_done()
            return False
        try:
            with self._lock:
                self._play_proc = subprocess.Popen(play, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            proc = self._play_proc
            proc.wait()
        except Exception as exc:  # noqa: BLE001
            log.error("Wiedergabe-Fehler: %s", exc)
        finally:
            try:
                os.unlink(wav)
            except OSError:
                pass
            if on_done:
                on_done()
        return True

    def stop(self) -> None:
        with self._lock:
            if self._play_proc and self._play_proc.poll() is None:
                try:
                    self._play_proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
            self._play_proc = None

    @staticmethod
    def estimate_duration_s(text: str) -> float:
        words = max(1, len((text or "").split()))
        return min(45.0, max(1.2, words / 2.4))


# --------------------------------------------------------------------------
# Higgs (hochwertig, GPU) — Minervas eigener Daemon auf separatem Port
# --------------------------------------------------------------------------
class HiggsTTS(BaseTTS):
    def __init__(self, url: str = "http://127.0.0.1:8761", voice: str = "minerva",
                 autostart: bool = True) -> None:
        self.url = url.rstrip("/")
        self.voice = voice
        self.autostart = autostart
        self.port = url.rsplit(":", 1)[-1].split("/")[0] if ":" in url else "8761"
        self._proc: Optional[subprocess.Popen] = None

    def health(self) -> Optional[dict]:
        try:
            import httpx

            r = httpx.get(f"{self.url}/health", timeout=2.5)
            if r.status_code == 200:
                return r.json()
        except Exception:
            return None
        return None

    def ensure_running(self, wait_s: float = 30.0) -> bool:
        if self.health() is not None:
            return True
        if not self.autostart:
            return False
        server = TTS_SERVER_DIR / "server.py"
        if not server.exists():
            return False
        py = TTS_SERVER_DIR / ".venv" / "bin" / "python"
        interp = str(py) if py.exists() else sys.executable
        env = dict(os.environ)
        env["TTS_PORT"] = str(self.port)
        env["TTS_VOICE"] = self.voice
        try:
            self._proc = subprocess.Popen(
                [interp, "server.py"], cwd=str(TTS_SERVER_DIR),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Higgs-Autostart fehlgeschlagen: %s", exc)
            return False
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if self.health() is not None:
                return True
            time.sleep(1.0)
        return self.health() is not None

    def speak(self, text: str, on_done: OnDone = None) -> bool:
        text = (text or "").strip()
        if not text:
            if on_done:
                on_done()
            return False
        ok = False
        if self.ensure_running():
            try:
                import httpx

                r = httpx.post(f"{self.url}/speak", json={"text": text}, timeout=10)
                ok = r.status_code == 200
            except Exception as exc:  # noqa: BLE001
                log.warning("Higgs /speak fehlgeschlagen: %s", exc)
        # Higgs spielt asynchron; Ende schätzen wir und rufen dann on_done.
        if on_done:
            dur = self.estimate_duration_s(text)
            threading.Timer(dur, on_done).start()
        return ok

    def stop(self) -> None:
        try:
            import httpx

            httpx.post(f"{self.url}/stop", timeout=3)
        except Exception:
            pass


def build_tts(cfg) -> BaseTTS:
    backend = cfg.get("voice.tts_backend", "piper")
    if backend == "higgs":
        log.info("TTS: Higgs (%s, Stimme=%s)", cfg.get("voice.tts_url"), cfg.get("voice.tts_voice"))
        return HiggsTTS(
            url=cfg.get("voice.tts_url", "http://127.0.0.1:8761"),
            voice=cfg.get("voice.tts_voice", "minerva"),
            autostart=cfg.get("voice.tts_autostart", True),
        )
    log.info("TTS: Piper (%s)", cfg.get("voice.piper_model"))
    return PiperTTS(
        model_path=cfg.get("voice.piper_model", "~/.minerva/voices/de_DE-kerstin-low.onnx"),
        length_scale=cfg.get("voice.piper_length_scale", 1.0),
    )


# Rückwärtskompatibler Alias
TTSClient = HiggsTTS
