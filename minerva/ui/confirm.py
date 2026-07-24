"""Thread-sichere Bestätigungs-Abfrage für gefährliche Aktionen.

Der Guard läuft im Worker-Thread und ruft confirm(...) synchron auf. Diese
Klasse marshallt die Anfrage in den GUI-Thread (per Qt-Signal), zeigt einen
Dialog und blockiert den Worker, bis der Nutzer entscheidet (oder Timeout).
"""
from __future__ import annotations

import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QMessageBox

from . import theme


class ConfirmController(QObject):
    _request = pyqtSignal(str, str, str, object)  # title, detail, risk, token

    def __init__(self, parent=None, timeout_s: int = 60) -> None:
        super().__init__(parent)
        self.timeout_s = timeout_s
        self._request.connect(self._show_dialog)

    # Wird im Worker-Thread aufgerufen (blockiert bis Antwort).
    def confirm(self, title: str, detail: str, risk: str) -> bool:
        token = {"event": threading.Event(), "result": False}
        self._request.emit(title, detail, risk, token)
        ok = token["event"].wait(timeout=self.timeout_s)
        if not ok:
            return False
        return bool(token["result"])

    def _show_dialog(self, title: str, detail: str, risk: str, token: dict) -> None:
        box = QMessageBox()
        box.setWindowTitle("MINERVA — Bestätigung")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"<b>{title}</b>")
        box.setInformativeText(f"Risiko: {risk}\n\n{detail}")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        result = box.exec()
        token["result"] = result == QMessageBox.StandardButton.Yes
        token["event"].set()
