"""Einstiegspunkt für MINERVA.

  python -m minerva            # native GUI (Orb + HUD + Tray)
  python -m minerva --cli      # Text-REPL im Terminal (kein Qt/Audio) — gut zum Testen
  python -m minerva --no-voice # GUI ohne Sprache

Optionen überschreiben die Konfiguration für diesen Start.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import LOG_DIR, __version__, ensure_dirs
from .config import load_config, resolve_brain_backend


def _setup_logging(verbose: bool) -> None:
    ensure_dirs()
    level = logging.DEBUG if verbose else logging.INFO
    handlers = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(logging.FileHandler(LOG_DIR / "minerva.log", encoding="utf-8"))
    except Exception:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # Externe Bibliotheken leiser stellen
    for noisy in ("httpx", "httpcore", "urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _apply_cli_overrides(cfg, args) -> None:
    if args.backend:
        cfg.set("brain.backend", args.backend)
    if args.model:
        # Modell für das aufgelöste Backend setzen.
        if resolve_brain_backend(cfg) == "anthropic":
            cfg.set("brain.anthropic_model", args.model)
        else:
            cfg.set("brain.model", args.model)
    if args.mode:
        cfg.set("safety.mode", args.mode)
    if args.no_voice:
        cfg.set("voice.enabled", False)


def run_gui(cfg, no_voice: bool) -> int:
    # XWayland/xcb bevorzugen: zuverlässiges Overlay, Positionierung, Hotkeys.
    if "QT_QPA_PLATFORM" not in os.environ and os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    from PyQt6.QtWidgets import QApplication

    from .app import JarvisApp

    qapp = QApplication(sys.argv)
    qapp.setApplicationName("MINERVA")
    qapp.setQuitOnLastWindowClosed(False)  # lebt im Tray weiter

    JarvisApp(cfg, qapp, no_voice=no_voice)
    return qapp.exec()


def run_cli(cfg) -> int:
    """Headless-Text-REPL: nützlich ohne Display/Audio und zum Testen."""
    from .brain.factory import build_backend
    from .core.orchestrator import Orchestrator
    from .memory import ConversationMemory
    from .rag_service import RagService
    from .safety import Guard
    from .skills import SkillManager
    from .tools.registry import ToolContext, build_default_registry

    guard = Guard(
        mode=cfg.get("safety.mode", "guarded"),
        hard_denylist=cfg.get("safety.hard_denylist", []),
        audit=cfg.get("safety.audit", True),
    )

    def _cli_confirm(title: str, detail: str, risk: str) -> bool:
        print(f"\n⚠  Bestätigung nötig [{risk}]: {title}\n   {detail}")
        try:
            return input("   Ausführen? [j/N] ").strip().lower() in ("j", "y", "ja", "yes")
        except EOFError:
            return False

    guard.set_confirm_fn(_cli_confirm)

    backend = build_backend(cfg)
    registry = build_default_registry(cfg)
    skills = SkillManager()
    rag = RagService(cfg) if cfg.get("tools.rag_enabled", True) else None
    from .memories import MemoryStore

    memories = (
        MemoryStore(cfg.get("memories.dir", "~/.minerva/memories"),
                    max_inject_chars=cfg.get("memories.max_inject_chars", 4000))
        if cfg.get("memories.enabled", True) else None
    )
    workdir = cfg.get("tools.workdir")
    ctx = ToolContext(
        cfg=cfg, guard=guard,
        workdir=Path(workdir).expanduser() if workdir else Path.home(),
    )
    ctx.backend, ctx.skill_manager, ctx.registry, ctx.rag = backend, skills, registry, rag
    ctx.memories = memories
    ctx.emit = lambda et, txt: print(f"   · [{et}] {txt}")
    for m in skills.load_all(registry):
        print(f"   · skill: {m}")

    orch = Orchestrator(cfg, backend, registry, ctx, ConversationMemory())

    print(f"\n◈ MINERVA (CLI) · Gehirn: {resolve_brain_backend(cfg)} · Modus: {guard.mode}")
    print("  Tippe deine Nachricht (oder 'exit').\n")
    while True:
        try:
            text = input("Sie ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("exit", "quit", ":q"):
            break
        print("MINERVA ▸ ", end="", flush=True)
        streamed = {"any": False}

        def _tok(t: str) -> None:
            streamed["any"] = True
            print(t, end="", flush=True)

        final = orch.handle(text, on_token=_tok, emit=lambda et, txt: None, on_state=lambda s: None)
        # Falls nichts gestreamt wurde (z. B. nur Tool-Turns), finalen Text zeigen.
        if not streamed["any"] and final:
            print(final, end="")
        print("\n")
    if rag:
        rag.close()
    return 0


def run_selftest(cfg) -> int:
    """Validierungslauf für die Selbst-Upgrade-Pipeline.

    Prüft, dass der gesamte Code fehlerfrei kompiliert/importiert, die
    Tool-Registry aufgebaut werden kann und die Kernobjekte konstruierbar sind.
    Exit 0 = gesund, 1 = defekt. (Kein Netzwerk/LLM nötig.)
    """
    import compileall
    import traceback
    from pathlib import Path

    from . import PACKAGE_ROOT

    ok = compileall.compile_dir(str(PACKAGE_ROOT), quiet=1, maxlevels=10)
    if not ok:
        print("SELFTEST FAIL: Kompilierung fehlgeschlagen")
        return 1
    try:
        from .safety import Guard
        from .tools.registry import ToolContext, build_default_registry
        from .core.orchestrator import Orchestrator  # noqa: F401
        from .memory import ConversationMemory  # noqa: F401
        from .memories import MemoryStore  # noqa: F401
        from .voice.tts import build_tts  # noqa: F401

        reg = build_default_registry(cfg)
        names = reg.names()
        if len(names) < 10:
            print(f"SELFTEST FAIL: nur {len(names)} Werkzeuge registriert")
            return 1
        Guard(mode="guarded")
        ToolContext(cfg=cfg, guard=Guard(mode="guarded"), workdir=Path.home())
        print(f"SELFTEST OK: {len(names)} Werkzeuge, alle Module importierbar")
        return 0
    except Exception as exc:  # noqa: BLE001
        print("SELFTEST FAIL:", exc)
        traceback.print_exc()
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="minerva", description="Minerva — lokaler Sprach-Assistent.")
    parser.add_argument("--cli", action="store_true", help="Text-REPL statt GUI.")
    parser.add_argument("--selftest", action="store_true", help="Validierungslauf (für Selbst-Upgrade).")
    parser.add_argument("--no-voice", action="store_true", help="GUI ohne Sprachein-/ausgabe.")
    parser.add_argument("--backend", choices=["auto", "ollama", "anthropic"], help="Gehirn-Backend.")
    parser.add_argument("--model", help="Modellname für das gewählte Backend.")
    parser.add_argument("--mode", choices=["guarded", "readonly", "yolo"], help="Sicherheitsmodus.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Ausführliche Logs.")
    parser.add_argument("--version", action="version", version=f"MINERVA {__version__}")
    args = parser.parse_args()

    _setup_logging(args.verbose)
    cfg = load_config()
    _apply_cli_overrides(cfg, args)

    if args.selftest:
        return run_selftest(cfg)
    if args.cli:
        return run_cli(cfg)
    return run_gui(cfg, no_voice=args.no_voice)


if __name__ == "__main__":
    raise SystemExit(main())
