"""Computer-Steuerung: Bildschirm sehen, klicken, tippen, Tasten, Zwischenablage.

Unter GNOME/Wayland gelten Einschränkungen bei globaler Eingabe-Injektion.
Screenshots laufen über mehrere Fallbacks inkl. GNOME-Shell-Portal (gdbus).
Maus/Tastatur nutzen xdotool (funktioniert für XWayland-Fenster & Terminal).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .. import MINERVA_HOME
from ..safety.guard import Risk
from .registry import Tool, ToolContext, ToolResult

SHOT_DIR = MINERVA_HOME / "screenshots"


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _desktop_file_exists(name: str) -> bool:
    name = name if name.endswith(".desktop") else f"{name}.desktop"
    dirs = [
        os.path.expanduser("~/.local/share/applications"),
        "/usr/share/applications",
        "/usr/local/share/applications",
        "/var/lib/flatpak/exports/share/applications",
    ]
    return any(os.path.exists(os.path.join(d, name)) for d in dirs)


def _capture_screenshot(dest: Path) -> tuple[bool, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 1) gnome-screenshot
    if _have("gnome-screenshot"):
        r = subprocess.run(["gnome-screenshot", "-f", str(dest)], capture_output=True, text=True)
        if r.returncode == 0 and dest.exists():
            return True, "gnome-screenshot"
    # 2) GNOME Shell Screenshot-Portal via gdbus (Wayland-tauglich)
    if _have("gdbus"):
        r = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.gnome.Shell.Screenshot",
                "--object-path", "/org/gnome/Shell/Screenshot",
                "--method", "org.gnome.Shell.Screenshot.Screenshot",
                "true", "false", str(dest),
            ],
            capture_output=True, text=True,
        )
        if "true" in (r.stdout or "").lower() and dest.exists():
            return True, "gnome-shell-portal"
    # 3) grim (wlroots)
    if _have("grim"):
        r = subprocess.run(["grim", str(dest)], capture_output=True, text=True)
        if r.returncode == 0 and dest.exists():
            return True, "grim"
    # 4) ImageMagick import (X11/XWayland)
    if _have("import"):
        r = subprocess.run(["import", "-window", "root", str(dest)], capture_output=True, text=True)
        if r.returncode == 0 and dest.exists():
            return True, "import"
    # 5) scrot
    if _have("scrot"):
        r = subprocess.run(["scrot", str(dest)], capture_output=True, text=True)
        if r.returncode == 0 and dest.exists():
            return True, "scrot"
    return False, "kein Screenshot-Backend verfügbar"


class ScreenshotTool(Tool):
    name = "see_screen"
    description = (
        "Macht einen Screenshot des Bildschirms. Wenn 'question' gesetzt ist, beschreibt "
        "ein lokales Vision-Modell (Ollama) den Bildschirm bzw. beantwortet die Frage dazu. "
        "So kann MINERVA sehen, was gerade auf dem Bildschirm ist."
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Optionale Frage zum Bildschirminhalt (z. B. 'Welches Fenster ist offen?').",
            },
            "vision_model": {"type": "string", "description": "Ollama-Vision-Modell (Default llama3.2-vision:11b)."},
        },
    }

    def run(self, args, ctx: ToolContext):
        # DANGEROUS: der Bildschirm kann Passwörter, private Nachrichten und
        # Bankdaten zeigen, und mit 'question' geht das Bild an ein Modell.
        # Vorher fehlte hier jede Guard-Abfrage — siehe Fund F7.
        question = args.get("question")
        decision = ctx.guard.review(
            "computer", "Bildschirm aufnehmen",
            f"question={question!r}" if question else "(nur Screenshot)",
            Risk.DANGEROUS,
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")

        ts = time.strftime("%Y%m%d-%H%M%S")
        dest = SHOT_DIR / f"shot-{ts}.png"
        ok, backend = _capture_screenshot(dest)
        if not ok:
            return ToolResult(False, f"Screenshot fehlgeschlagen: {backend}")
        info = f"Screenshot gespeichert: {dest} (via {backend})"

        if not question:
            return ToolResult(True, info)

        model = args.get("vision_model", "llama3.2-vision:11b")
        try:
            import ollama

            client = ollama.Client(host=ctx.cfg.get("brain.ollama_host", "http://127.0.0.1:11434"))
            resp = client.chat(
                model=model,
                messages=[{"role": "user", "content": question, "images": [str(dest)]}],
            )
            answer = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
        except Exception as exc:  # noqa: BLE001
            return ToolResult(True, f"{info}\n(Bildanalyse fehlgeschlagen: {exc})")
        return ToolResult(True, f"{info}\nBeobachtung: {answer.strip()}")


class ClickTool(Tool):
    name = "click"
    description = "Bewegt die Maus zu (x, y) und klickt. button: 1=links, 2=mitte, 3=rechts. double=true für Doppelklick."
    parameters = {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "button": {"type": "integer", "description": "1/2/3 (Default 1)."},
            "double": {"type": "boolean"},
        },
        "required": ["x", "y"],
    }

    def run(self, args, ctx: ToolContext):
        x, y = int(args["x"]), int(args["y"])
        button = int(args.get("button", 1))
        clicks = 2 if args.get("double") else 1
        decision = ctx.guard.review("computer", "Mausklick", f"({x},{y}) button={button}", Risk.MODERATE)
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        if not _have("xdotool"):
            return ToolResult(False, "xdotool nicht installiert.")
        cmd = ["xdotool", "mousemove", str(x), str(y), "click", "--repeat", str(clicks), str(button)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return ToolResult(r.returncode == 0, f"Klick auf ({x},{y})" if r.returncode == 0 else r.stderr)


class TypeTextTool(Tool):
    name = "type_text"
    description = "Tippt Text an der aktuellen Fokusposition (als würde man auf der Tastatur schreiben)."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def run(self, args, ctx: ToolContext):
        text = args.get("text", "")
        decision = ctx.guard.review("computer", "Text tippen", text[:120], Risk.DANGEROUS)
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        if not _have("xdotool"):
            return ToolResult(False, "xdotool nicht installiert.")
        r = subprocess.run(["xdotool", "type", "--clearmodifiers", "--", text], capture_output=True, text=True)
        return ToolResult(r.returncode == 0, f"Getippt ({len(text)} Zeichen)" if r.returncode == 0 else r.stderr)


class KeyPressTool(Tool):
    name = "press_key"
    description = "Drückt eine Tastenkombination (xdotool-Syntax), z. B. 'ctrl+c', 'alt+Tab', 'Return', 'super'."
    parameters = {
        "type": "object",
        "properties": {"keys": {"type": "string", "description": "z. B. 'ctrl+shift+t'."}},
        "required": ["keys"],
    }

    def run(self, args, ctx: ToolContext):
        keys = args.get("keys", "")
        # DANGEROUS wie type_text: `xdotool key a b c` sendet Zeichen, press_key
        # war damit die laxere Tür zur selben Wirkung. Siehe Fund F9.
        decision = ctx.guard.review("computer", "Taste drücken", keys, Risk.DANGEROUS)
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        if not _have("xdotool"):
            return ToolResult(False, "xdotool nicht installiert.")
        r = subprocess.run(["xdotool", "key", "--clearmodifiers", keys], capture_output=True, text=True)
        return ToolResult(r.returncode == 0, f"Gedrückt: {keys}" if r.returncode == 0 else r.stderr)


class ClipboardTool(Tool):
    name = "clipboard"
    description = "Liest ('get') oder setzt ('set') die Zwischenablage."
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["get", "set"]},
            "text": {"type": "string", "description": "Nur bei action=set."},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext):
        action = args.get("action")
        # Vorher ohne jede Guard-Abfrage — siehe Fund F8. 'get' ist der
        # riskantere Weg: Passwort-Manager legen Geheimnisse in die
        # Zwischenablage, das Lesen ist also ein Exfiltrationspfad. 'set'
        # verändert Systemzustand und gehört damit nicht in den readonly-Modus.
        decision = ctx.guard.review(
            "computer",
            "Zwischenablage lesen" if action == "get" else "Zwischenablage setzen",
            f"action={action!r}",
            Risk.DANGEROUS if action == "get" else Risk.MODERATE,
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")

        if action == "get":
            for cmd in (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"]):
                if _have(cmd[0]):
                    r = subprocess.run(cmd, capture_output=True, text=True)
                    if r.returncode == 0:
                        return ToolResult(True, r.stdout)
            return ToolResult(False, "Keine Zwischenablage-Tool (wl-paste/xclip).")
        if action == "set":
            text = args.get("text", "")
            for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
                if _have(cmd[0]):
                    try:
                        subprocess.run(cmd, input=text, text=True, check=True)
                        return ToolResult(True, f"Zwischenablage gesetzt ({len(text)} Zeichen).")
                    except Exception:  # noqa: BLE001
                        continue
            return ToolResult(False, "Keine Zwischenablage-Tool (wl-copy/xclip).")
        return ToolResult(False, "action muss 'get' oder 'set' sein.")


class NotifyTool(Tool):
    name = "notify"
    description = "Zeigt eine Desktop-Benachrichtigung an (notify-send)."
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "message": {"type": "string"},
        },
        "required": ["message"],
    }

    def run(self, args, ctx: ToolContext):
        title = args.get("title", "MINERVA")
        message = args.get("message", "")
        # SAFE, aber protokolliert: harmlos in der Wirkung, gehört trotzdem ins
        # Audit-Log. Autorisierung vor Fähigkeitsprüfung (Fund F8, geringes Gewicht).
        decision = ctx.guard.review(
            "computer", "Benachrichtigung anzeigen", f"{title}: {message[:80]}", Risk.SAFE
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        if not _have("notify-send"):
            return ToolResult(False, "notify-send nicht installiert.")
        subprocess.run(["notify-send", title, message], capture_output=True)
        return ToolResult(True, "Benachrichtigung angezeigt.")


class OpenAppTool(Tool):
    name = "open_app"
    description = (
        "Öffnet eine Anwendung, eine Datei oder eine URL mit der zuständigen Desktop-App "
        "(losgelöst, blockiert nicht). Beispiele: target='firefox', target='~/notiz.txt', "
        "target='https://example.com'. Nutze dies, um Programme/Dokumente für den Nutzer zu öffnen."
    )
    parameters = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "App-Name, Dateipfad oder URL."},
            "args": {"type": "string", "description": "Optionale Argumente für die App."},
        },
        "required": ["target"],
    }

    def run(self, args, ctx: ToolContext):
        target = (args.get("target") or "").strip()
        if not target:
            return ToolResult(False, "Kein Ziel angegeben.")
        decision = ctx.guard.review("computer", "App/Datei öffnen", target, Risk.MODERATE)
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")

        extra = (args.get("args") or "").split() if args.get("args") else []
        is_url = target.startswith(("http://", "https://"))
        path = os.path.expanduser(target)
        is_path = os.path.exists(path)

        try:
            if is_url or is_path:
                launch = os.path.expanduser(target) if is_path else target
                subprocess.Popen(["xdg-open", launch], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return ToolResult(True, f"Geöffnet: {launch}")
            # Sonst als ausführbares Programm behandeln.
            if _have(target):
                subprocess.Popen([target, *extra], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, cwd=str(ctx.workdir))
                return ToolResult(True, f"Programm gestartet: {target} {' '.join(extra)}".strip())
            # Fallback über gtk-launch — nur, wenn eine passende .desktop-Datei existiert.
            if _have("gtk-launch") and _desktop_file_exists(target):
                subprocess.Popen(["gtk-launch", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return ToolResult(True, f"Über gtk-launch gestartet: {target}")
            return ToolResult(False, f"Weder Programm noch Datei/URL gefunden: {target}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Öffnen fehlgeschlagen: {exc}")


class VolumeTool(Tool):
    name = "adjust_volume"
    description = (
        "Regelt die Lautstärke der Standard-Audioausgabe. action: 'set' (0-100), 'up', 'down', "
        "'mute', 'unmute'. Nutzt wpctl (PipeWire)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "up", "down", "mute", "unmute"]},
            "value": {"type": "integer", "description": "Prozent (bei set) bzw. Schrittweite (up/down)."},
        },
        "required": ["action"],
    }

    def run(self, args, ctx: ToolContext):
        action = args.get("action")
        # Verändert Systemzustand -> MODERATE, vorher ungeprüft (Fund F8).
        decision = ctx.guard.review(
            "computer", "Lautstärke ändern", f"action={action!r}", Risk.MODERATE
        )
        if not decision.allowed:
            return ToolResult(False, f"Abgelehnt: {decision.reason}")
        if not _have("wpctl"):
            return ToolResult(False, "wpctl (PipeWire) nicht verfügbar.")
        sink = "@DEFAULT_AUDIO_SINK@"
        value = int(args.get("value", 5))
        try:
            if action == "set":
                pct = max(0, min(100, int(args.get("value", 50))))
                subprocess.run(["wpctl", "set-volume", sink, f"{pct}%"], check=True)
                return ToolResult(True, f"Lautstärke auf {pct}% gesetzt.")
            if action == "up":
                subprocess.run(["wpctl", "set-volume", sink, f"{value}%+"], check=True)
                return ToolResult(True, f"Lautstärke +{value}%.")
            if action == "down":
                subprocess.run(["wpctl", "set-volume", sink, f"{value}%-"], check=True)
                return ToolResult(True, f"Lautstärke -{value}%.")
            if action == "mute":
                subprocess.run(["wpctl", "set-mute", sink, "1"], check=True)
                return ToolResult(True, "Stummgeschaltet.")
            if action == "unmute":
                subprocess.run(["wpctl", "set-mute", sink, "0"], check=True)
                return ToolResult(True, "Ton wieder an.")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(False, f"Lautstärke-Fehler: {exc}")
        return ToolResult(False, "Unbekannte action.")
