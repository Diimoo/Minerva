"""F5: defekte config.yaml muss eine lesbare Fehlermeldung liefern.

Die Datei ist ausdrücklich zum Handeditieren gedacht ("Referenz zum Editieren",
config.py). Ein Tippfehler darf keinen rohen yaml-Traceback produzieren, sondern
muss sagen, welche Datei klemmt.
"""
from __future__ import annotations

import pytest

from minerva.config import ConfigError, load_config


BROKEN_YAML = 'brain:\n  backend: auto\n   model: "kaputt\n'


def test_broken_yaml_raises_configerror(tmp_path):
    """F5: ConfigError statt yaml.ScannerError."""
    p = tmp_path / "config.yaml"
    p.write_text(BROKEN_YAML, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_configerror_names_the_file(tmp_path):
    """F5: Die Meldung muss den Pfad enthalten, sonst hilft sie nicht."""
    p = tmp_path / "config.yaml"
    p.write_text(BROKEN_YAML, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert str(p) in str(exc.value)


def test_yaml_scanner_error_is_not_leaked(tmp_path):
    """F5: Der rohe Parser-Fehler darf nicht nach oben durchschlagen."""
    import yaml

    p = tmp_path / "config.yaml"
    p.write_text(BROKEN_YAML, encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_config(p)
    assert not isinstance(exc.value, yaml.YAMLError)


def test_valid_config_still_loads(tmp_path):
    """Regressionsschutz: gültige Konfiguration wird gemergt."""
    p = tmp_path / "config.yaml"
    p.write_text('brain:\n  backend: ollama\n  model: testmodel\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.get("brain.backend") == "ollama"
    assert cfg.get("brain.model") == "testmodel"
    # Nicht gesetzte Werte kommen aus den DEFAULTS.
    assert cfg.get("brain.max_tool_iterations") == 12


def test_missing_config_uses_defaults(tmp_path):
    """Regressionsschutz: fehlende Datei ist kein Fehler."""
    cfg = load_config(tmp_path / "nicht-da.yaml")
    assert cfg.get("brain.backend") == "auto"


def test_empty_config_is_not_an_error(tmp_path):
    """Randfall: leere Datei -> reine DEFAULTS, kein Fehler."""
    p = tmp_path / "config.yaml"
    p.write_text("", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.get("brain.backend") == "auto"
