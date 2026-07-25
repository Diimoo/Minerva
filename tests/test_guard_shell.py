"""Guard: Shell-Klassifikation.

Deckt die Funde F1, F2 und F4 aus .bughunter/findings/safety-guard.md ab.
Jeder Test war vor dem Fix rot.
"""
from __future__ import annotations

import pytest

from minerva.safety.guard import Risk
from tests.conftest import allowed


# --------------------------------------------------------------------------
# F1 — katastrophale Befehle müssen unabhängig von der Schreibweise blocken,
#      auch im yolo-Modus (dort ist die Sperrliste die einzige Schicht).
# --------------------------------------------------------------------------

CATASTROPHIC = [
    "rm -rf /",                     # Literalform (blockte schon vorher)
    "rm -fr /",                     # Flags getauscht
    "rm  -rf  /",                   # doppelte Leerzeichen
    "rm -r -f /",                   # Flags getrennt
    "rm --recursive --force /",     # Langform
    "rm -rf '/'",                   # gequotet
    'rm -rf "/"',                   # doppelt gequotet
    "rm -rf ~",
    "rm -rf $HOME",
    "cd / && rm -rf .",             # anderes Ziel, gleiche Wirkung
    "ls; rm -rf /",                 # verkettet
    "rm -rf /*",
]


@pytest.mark.parametrize("cmd", CATASTROPHIC)
def test_catastrophic_delete_blocked_in_yolo(make_guard, cmd):
    """F1: Auch yolo darf einen rekursiven Root-Löschbefehl nicht durchlassen."""
    g = make_guard(mode="yolo")
    assert not allowed(g, cmd), f"yolo ließ {cmd!r} durch"


@pytest.mark.parametrize("cmd", CATASTROPHIC)
def test_catastrophic_delete_is_forbidden_risk(make_guard, cmd):
    """F1: Einstufung muss FORBIDDEN sein, nicht bloß DANGEROUS."""
    g = make_guard(mode="yolo")
    risk, _ = g.classify_shell(cmd)
    assert risk == Risk.FORBIDDEN, f"{cmd!r} -> {risk.value}"


POWER_STATE = [
    "shutdown -h now",
    "reboot",
    "poweroff",
    "halt",
    "systemctl poweroff",
    "systemctl reboot",
    "systemctl halt",               # Synonym, stand nicht auf der Sperrliste
]


@pytest.mark.parametrize("cmd", POWER_STATE)
def test_power_state_blocked_in_yolo(make_guard, cmd):
    """F1: Herunterfahren/Neustart strukturell blocken, nicht per Wortliste."""
    g = make_guard(mode="yolo")
    assert not allowed(g, cmd), f"yolo ließ {cmd!r} durch"


def test_normal_delete_still_possible(make_guard):
    """Kein Kollateralschaden: gezieltes Löschen bleibt erlaubt (in yolo)."""
    g = make_guard(mode="yolo")
    assert allowed(g, "rm -rf ./build")
    assert allowed(g, "rm /tmp/scratch.txt")


# --------------------------------------------------------------------------
# F2 — READONLY_PREFIXES prüft nur das erste Token. Argumente, die mutieren,
#      dürfen die SAFE-Einstufung nicht erben.
# --------------------------------------------------------------------------

MUTATING_WITH_READONLY_PREFIX = [
    "find . -delete",
    "find /home/ahmed -name '*.txt' -delete",
    "find . -exec rm {} ;",
    "find . -execdir rm {} ;",
    "ip link set eth0 down",
    "ip addr del 10.0.0.1/24 dev eth0",
    "history -c",
]


@pytest.mark.parametrize("cmd", MUTATING_WITH_READONLY_PREFIX)
def test_mutating_args_are_not_safe(make_guard, cmd):
    """F2: SAFE muss bedeuten 'kann nichts verändern'."""
    g = make_guard(mode="readonly")
    risk, why = g.classify_shell(cmd)
    assert risk != Risk.SAFE, f"{cmd!r} wurde als SAFE eingestuft ({why})"


@pytest.mark.parametrize("cmd", MUTATING_WITH_READONLY_PREFIX)
def test_mutating_args_blocked_in_readonly(make_guard, cmd):
    """F2: Im readonly-Modus darf davon nichts durchlaufen."""
    g = make_guard(mode="readonly")
    assert not allowed(g, cmd), f"readonly ließ {cmd!r} durch"


GENUINELY_READONLY = [
    "ls -la",
    "cat /etc/hostname",
    "pwd",
    "find . -name '*.py'",          # find ohne Aktions-Flag bleibt lesend
    "find /etc -type f",
    "ip addr show",                 # ip ohne mutierendes Subkommando
    "ip link show",
    "grep -rn TODO .",
    "wc -l setup.py",
]


@pytest.mark.parametrize("cmd", GENUINELY_READONLY)
def test_readonly_commands_stay_safe(make_guard, cmd):
    """Regressionsschutz: echte Lesebefehle müssen SAFE bleiben."""
    g = make_guard(mode="readonly")
    risk, why = g.classify_shell(cmd)
    assert risk == Risk.SAFE, f"{cmd!r} -> {risk.value} ({why})"
    assert allowed(g, cmd)


# --------------------------------------------------------------------------
# F4 — Path(t).name reduzierte '/tmp/evil/ls' auf 'ls' und erbte damit SAFE.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "/tmp/evil/ls",
    "/home/ahmed/ls",
    "./ls",
    "../ls",
    "/tmp/cat /etc/passwd",
])
def test_untrusted_path_not_safe(make_guard, cmd):
    """F4: Ein harmloser Basisname macht ein fremdes Programm nicht lesend."""
    g = make_guard(mode="readonly")
    risk, why = g.classify_shell(cmd)
    assert risk != Risk.SAFE, f"{cmd!r} wurde als SAFE eingestuft ({why})"


@pytest.mark.parametrize("cmd", ["/bin/ls", "/usr/bin/ls -la", "/usr/bin/cat x"])
def test_trusted_system_path_stays_safe(make_guard, cmd):
    """Regressionsschutz: Systempfade bleiben lesend."""
    g = make_guard(mode="readonly")
    risk, why = g.classify_shell(cmd)
    assert risk == Risk.SAFE, f"{cmd!r} -> {risk.value} ({why})"


# --------------------------------------------------------------------------
# Bestehendes Verhalten, das nicht kaputtgehen darf
# --------------------------------------------------------------------------

def test_redirect_disqualifies_safe(make_guard):
    """Umleitung/Pipe hebt SAFE auf (Verhalten vor dem Fix, bleibt)."""
    g = make_guard(mode="readonly")
    assert g.classify_shell("cat a > b")[0] != Risk.SAFE
    assert g.classify_shell("ls | tee out")[0] != Risk.SAFE


def test_env_prefix_is_skipped(make_guard):
    """VAR=wert vor dem Befehl darf die Erkennung nicht verschieben."""
    g = make_guard(mode="readonly")
    assert g.classify_shell("LC_ALL=C ls -la")[0] == Risk.SAFE


def test_guarded_dangerous_needs_confirmation(make_guard):
    """guarded: DANGEROUS ohne Zustimmung wird abgelehnt."""
    g = make_guard(mode="guarded", confirm=False)
    assert not allowed(g, "chmod 777 /etc/passwd")
    g_ok = make_guard(mode="guarded", confirm=True)
    assert allowed(g_ok, "chmod 777 /etc/passwd")


def test_missing_confirm_fn_is_failsafe(make_guard):
    """Ohne Bestätigungs-Callback wird DANGEROUS abgelehnt (fail-safe)."""
    g = make_guard(mode="guarded")   # kein confirm_fn
    assert not allowed(g, "chmod 777 /etc/passwd")


# --------------------------------------------------------------------------
# F6 — die Sperrliste war ein roher Substring-Vergleich und traf damit auch
#      harmlose Befehle, die einen Eintrag zufällig als Präfix enthalten.
#      (Vorbestehend, beim Verifizieren von F1 aufgefallen.)
# --------------------------------------------------------------------------

FALSE_POSITIVES = [
    "rm -rf /tmp/scratch",        # enthält 'rm -rf /' als Präfix
    "rm -rf /home/ahmed/build",
    "rm -rf /var/tmp/cache",
    "cat shutdown-notes.txt",     # enthält 'shutdown'
    "grep reboot /var/log/syslog",
    "echo poweroff >> notizen.md",
]


@pytest.mark.parametrize("cmd", FALSE_POSITIVES)
def test_denylist_does_not_match_substrings(make_guard, cmd):
    """F6: Ein Sperrlisten-Eintrag darf nur ganze Token treffen."""
    g = make_guard(mode="yolo")
    risk, why = g.classify_shell(cmd)
    assert risk != Risk.FORBIDDEN, f"{cmd!r} fälschlich gesperrt ({why})"


# --------------------------------------------------------------------------
# Die strukturellen Sperren tragen jetzt, was vorher die Sperrliste per
# Substring erwischte. Diese Fälle müssen ohne Sperrlisten-Eintrag blocken.
# --------------------------------------------------------------------------

STRUCTURALLY_CATASTROPHIC = [
    "mkfs.ext4 /dev/sdb1",        # 'mkfs' matcht als Token nicht mehr
    "mkfs -t ext4 /dev/sdb1",
    "fdisk /dev/sda",
    "wipefs -a /dev/sda",
    "dd if=/dev/zero of=/dev/sda",
    "cat image.iso > /dev/sda",
    ":(){ :|:& };:",              # Fork-Bombe
]


@pytest.mark.parametrize("cmd", STRUCTURALLY_CATASTROPHIC)
def test_structural_rules_block_without_denylist(make_guard, cmd):
    """Ohne jede Sperrliste blocken — die Struktur trägt, nicht die Wortliste."""
    g = make_guard(mode="yolo", denylist=[])
    risk, why = g.classify_shell(cmd)
    assert risk == Risk.FORBIDDEN, f"{cmd!r} -> {risk.value} ({why})"
    assert not allowed(g, cmd)


def test_dd_reading_is_not_catastrophic(make_guard):
    """Kein Kollateralschaden: dd ohne Geräte-Ziel bleibt bloß gefährlich."""
    g = make_guard(mode="yolo", denylist=[])
    risk, _ = g.classify_shell("dd if=/dev/zero of=./testfile bs=1M count=1")
    assert risk != Risk.FORBIDDEN
