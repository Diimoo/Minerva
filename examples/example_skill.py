"""Beispiel-Skill für MINERVA — als Vorlage/Referenz.

So sieht ein Skill aus. Kopiere diese Datei nach ~/.minerva/skills/ (oder lass
MINERVA sie über `create_skill` selbst erzeugen), dann steht das Werkzeug sofort
zur Verfügung.

Ein Skill muss GENAU EINE Funktion get_tools() bereitstellen, die eine Liste von
Tool-Instanzen zurückgibt. Jedes Tool hat:
  * name        — eindeutiger Werkzeugname (snake_case)
  * description — was es tut (das LLM entscheidet danach, ob es das Tool nutzt)
  * parameters  — JSON-Schema der Argumente
  * run(args, ctx) -> ToolResult(ok: bool, content: str)

Über ctx sind verfügbar: ctx.workdir (Path), ctx.cfg (Config), ctx.guard
(Sicherheit), ctx.emit (Statusmeldungen). Für gefährliche Aktionen IMMER
ctx.guard.review(...) verwenden.
"""
from minerva.tools.registry import Tool, ToolResult
from minerva.safety.guard import Risk


class DiceRollTool(Tool):
    name = "roll_dice"
    description = "Würfelt eine oder mehrere Würfel und gibt die Summe und Einzelwerte zurück."
    parameters = {
        "type": "object",
        "properties": {
            "sides": {"type": "integer", "description": "Seitenzahl je Würfel (Default 6)."},
            "count": {"type": "integer", "description": "Anzahl Würfel (Default 1)."},
        },
    }

    def run(self, args, ctx):
        # Deterministisch-genug ohne Zufallsmodul-Abhängigkeit: nutzt time-Bits.
        import time

        sides = max(2, int(args.get("sides", 6)))
        count = max(1, min(20, int(args.get("count", 1))))
        seed = int(time.time() * 1000)
        rolls = []
        for i in range(count):
            seed = (seed * 1103515245 + 12345 + i * 7919) & 0x7FFFFFFF
            rolls.append(seed % sides + 1)
        return ToolResult(True, f"Würfe: {rolls} · Summe: {sum(rolls)}")


def get_tools():
    return [DiceRollTool()]
