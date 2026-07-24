"""System-Tray-Icon mit Menü."""
from __future__ import annotations

import math

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap, QRadialGradient
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from . import theme


def make_orb_icon(size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cx = cy = size / 2
    grad = QRadialGradient(cx, cy, size * 0.5)
    grad.setColorAt(0.0, theme.ACCENT_BRIGHT)
    grad.setColorAt(0.6, theme.ACCENT)
    edge = QColor(theme.ACCENT); edge.setAlpha(0)
    grad.setColorAt(1.0, edge)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(grad)
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.setBrush(QColor(255, 255, 255, 230))
    r = size * 0.16
    p.drawEllipse(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
    p.end()
    return QIcon(pm)


class Tray(QSystemTrayIcon):
    def __init__(self, app_ctx, parent=None) -> None:
        super().__init__(make_orb_icon(), parent)
        self.app_ctx = app_ctx
        self.setToolTip("MINERVA")
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _build_menu(self) -> None:
        menu = QMenu()

        self.act_listen = QAction("🎤 Zuhören umschalten", menu)
        self.act_listen.triggered.connect(self.app_ctx.toggle_listen)
        menu.addAction(self.act_listen)

        act_hud = QAction("🖥  Konsole anzeigen/verbergen", menu)
        act_hud.triggered.connect(self.app_ctx.toggle_hud)
        menu.addAction(act_hud)

        act_orb = QAction("◈  Orb anzeigen/verbergen", menu)
        act_orb.triggered.connect(self.app_ctx.toggle_orb)
        menu.addAction(act_orb)

        menu.addSeparator()

        # Sicherheitsmodus
        mode_menu = menu.addMenu("🛡  Sicherheitsmodus")
        for mode, label in [("guarded", "Bewacht (Bestätigung)"),
                            ("readonly", "Nur lesen"),
                            ("yolo", "YOLO (keine Rückfragen)")]:
            a = QAction(label, mode_menu)
            a.triggered.connect(lambda _c, m=mode: self.app_ctx.set_safety_mode(m))
            mode_menu.addAction(a)

        menu.addSeparator()
        act_stop = QAction("⏹  Sprechen stoppen", menu)
        act_stop.triggered.connect(self.app_ctx.stop_speaking)
        menu.addAction(act_stop)

        act_quit = QAction("⏻  Beenden", menu)
        act_quit.triggered.connect(self.app_ctx.quit)
        menu.addAction(act_quit)

        self.setContextMenu(menu)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.app_ctx.toggle_hud()
