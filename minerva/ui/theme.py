"""Farben & Stile im MINERVA-„Arc"-Look (holografisches Cyan/Blau)."""
from __future__ import annotations

from PyQt6.QtGui import QColor

ACCENT = QColor(51, 200, 255)       # #33c8ff
ACCENT_DIM = QColor(30, 120, 165)
ACCENT_BRIGHT = QColor(150, 235, 255)
WARN = QColor(255, 170, 60)
DANGER = QColor(255, 80, 90)
OK = QColor(80, 230, 160)
BG = QColor(8, 14, 22)
BG_PANEL = QColor(12, 20, 30, 235)
TEXT = QColor(210, 235, 245)
MUTED = QColor(120, 150, 165)

# Zustandsfarben für den Orb.
STATE_COLORS = {
    "idle": ACCENT_DIM,
    "listening": ACCENT,
    "hearing": ACCENT_BRIGHT,
    "transcribing": QColor(90, 180, 255),
    "thinking": QColor(120, 130, 255),
    "speaking": QColor(70, 220, 200),
    "error": DANGER,
}


HUD_QSS = f"""
QWidget#hudRoot {{
    background: rgba(10, 16, 24, 235);
    border: 1px solid rgba(51, 200, 255, 90);
    border-radius: 16px;
}}
QLabel {{ color: rgb(210,235,245); }}
QLabel#title {{ color: rgb(150,235,255); font-size: 15px; font-weight: 600; letter-spacing: 2px; }}
QLabel#status {{ color: rgb(120,150,165); font-size: 11px; }}
QTextEdit {{
    background: rgba(6, 12, 18, 200);
    color: rgb(205,230,242);
    border: 1px solid rgba(51,200,255,50);
    border-radius: 10px;
    font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
    font-size: 12px;
    padding: 8px;
}}
QLineEdit {{
    background: rgba(6, 12, 18, 220);
    color: rgb(220,240,250);
    border: 1px solid rgba(51,200,255,110);
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 13px;
}}
QLineEdit:focus {{ border: 1px solid rgba(150,235,255,200); }}
QPushButton {{
    background: rgba(51,200,255,25);
    color: rgb(180,235,255);
    border: 1px solid rgba(51,200,255,120);
    border-radius: 9px;
    padding: 7px 14px;
    font-size: 12px;
}}
QPushButton:hover {{ background: rgba(51,200,255,55); }}
QPushButton:pressed {{ background: rgba(51,200,255,90); }}
QPushButton#danger {{ border-color: rgba(255,90,100,160); color: rgb(255,150,155); }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: rgba(51,200,255,90); border-radius: 4px; }}
"""
