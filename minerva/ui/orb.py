"""Der schwebende Arc-Reactor-Orb — visuelles Herz von MINERVA.

Ein rahmenloses, halbtransparentes Always-on-Top-Fenster, das den aktuellen
Zustand als Farbe/Animation zeigt und auf den Mikrofonpegel reagiert.
  * Klick        -> Zuhören an/aus
  * Doppelklick  -> HUD-Konsole ein/aus
  * Ziehen       -> verschieben
  * Rechtsklick  -> Kontextmenü
"""
from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QConicalGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

from . import theme


class AnimatedOrb(QWidget):
    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    context_requested = pyqtSignal(QPoint)

    def __init__(self, size: int = 140, parent=None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._state = "idle"
        self._level = 0.0            # geglätteter Mikrofonpegel 0..1
        self._target_level = 0.0
        self._phase = 0.0
        self._pulse = 0.0

        self._drag_pos: QPoint | None = None
        self._moved = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)        # ~30 fps

    # -- öffentliche API ---------------------------------------------------
    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def set_level(self, level: float) -> None:
        self._target_level = max(0.0, min(1.0, level))

    # -- Animation ---------------------------------------------------------
    def _tick(self) -> None:
        speed = {
            "idle": 0.6, "listening": 1.4, "hearing": 2.4,
            "transcribing": 1.8, "thinking": 3.2, "speaking": 2.0, "error": 1.0,
        }.get(self._state, 1.0)
        self._phase = (self._phase + 0.02 * speed) % (2 * math.pi)
        self._pulse = 0.5 + 0.5 * math.sin(self._phase * 2)
        # Pegel glätten
        self._level += (self._target_level - self._level) * 0.25
        self._target_level *= 0.9    # klingt ab, wenn keine neuen Werte kommen
        self.update()

    # -- Zeichnen ----------------------------------------------------------
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = self._size
        cx = cy = w / 2
        color = theme.STATE_COLORS.get(self._state, theme.ACCENT)

        base_r = w * 0.30
        react = base_r * (1.0 + 0.18 * self._level + 0.06 * self._pulse)

        # 1) äußerer Glow
        glow = QRadialGradient(cx, cy, w * 0.5)
        gc = QColor(color)
        gc.setAlpha(int(70 + 60 * self._pulse + 90 * self._level))
        glow.setColorAt(0.0, gc)
        edge = QColor(color)
        edge.setAlpha(0)
        glow.setColorAt(1.0, edge)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QRectF(0, 0, w, w))

        # 2) rotierende Arc-Ringe
        for i, (radius_f, span, width, direction, alpha) in enumerate(
            [(0.44, 90, 3.0, 1, 200), (0.40, 60, 2.0, -1, 150), (0.36, 120, 2.5, 1, 120)]
        ):
            r = w * radius_f
            rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
            start = (self._phase * direction * (60 + i * 25)) % 360
            pen = QPen(QColor(color.red(), color.green(), color.blue(), alpha))
            pen.setWidthF(width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            # QPainter.drawArc nutzt 1/16-Grad
            p.drawArc(rect, int(start * 16), int(span * 16))
            p.drawArc(rect, int((start + 180) * 16), int(span * 16))

        # 3) konischer „Scan"-Ring (dünner, dreht schnell)
        cg = QConicalGradient(cx, cy, math.degrees(self._phase) * 3)
        c1 = QColor(color); c1.setAlpha(0)
        c2 = QColor(theme.ACCENT_BRIGHT); c2.setAlpha(220)
        cg.setColorAt(0.0, c1)
        cg.setColorAt(0.15, c2)
        cg.setColorAt(0.3, c1)
        cg.setColorAt(1.0, c1)
        ring_r = w * 0.32
        pen = QPen()
        pen.setBrush(cg)
        pen.setWidthF(2.5)
        p.setPen(pen)
        p.drawEllipse(QRectF(cx - ring_r, cy - ring_r, 2 * ring_r, 2 * ring_r))

        # 4) Kern (Arc-Reactor)
        core = QRadialGradient(cx, cy, react)
        cc = QColor(theme.ACCENT_BRIGHT)
        cc.setAlpha(255)
        core.setColorAt(0.0, cc)
        mid = QColor(color); mid.setAlpha(230)
        core.setColorAt(0.55, mid)
        outer = QColor(color); outer.setAlpha(40)
        core.setColorAt(1.0, outer)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(core)
        p.drawEllipse(QRectF(cx - react, cy - react, 2 * react, 2 * react))

        # 5) innere „Triangel"-Blende (Arc-Reactor-Anmutung)
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 255, 255, 180))
        pen.setWidthF(1.4)
        p.setPen(pen)
        tri = QPainterPath()
        rr = react * 0.62
        for k in range(3):
            ang = self._phase * 0.5 + k * (2 * math.pi / 3)
            x = cx + rr * math.cos(ang)
            y = cy + rr * math.sin(ang)
            if k == 0:
                tri.moveTo(x, y)
            else:
                tri.lineTo(x, y)
        tri.closeSubpath()
        p.drawPath(tri)
        p.end()

    # -- Interaktion -------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved = False
            # Wayland-freundliches Verschieben:
            try:
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemMove()
                    self._moved = True  # System übernimmt Move
            except Exception:
                pass
        elif event.button() == Qt.MouseButton.RightButton:
            self.context_requested.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            new = event.globalPosition().toPoint() - self._drag_pos
            if (new - self.frameGeometry().topLeft()).manhattanLength() > 3:
                self._moved = True
            self.move(new)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._moved:
                self.clicked.emit()
            self._drag_pos = None

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
