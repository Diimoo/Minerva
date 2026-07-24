"""HUD-Konsole: Dialog-Verlauf, Werkzeug-Aktivität, Status und Texteingabe."""
from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import theme


def _hex(c: QColor) -> str:
    return f"#{c.red():02x}{c.green():02x}{c.blue():02x}"


class HudWindow(QWidget):
    submit_text = pyqtSignal(str)
    toggle_listen = pyqtSignal()
    stop_all = pyqtSignal()
    clear_chat = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("hudRoot")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(560, 620)
        self.setStyleSheet(theme.HUD_QSS)
        self._drag_pos: QPoint | None = None
        self._assistant_open = False
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(8)

        # Kopfzeile (ziehbar)
        header = QHBoxLayout()
        self.title_label = QLabel("◈ MINERVA")
        title = self.title_label
        title.setObjectName("title")
        self.status = QLabel("bereit")
        self.status.setObjectName("status")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.status)
        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(34)
        close_btn.clicked.connect(self.hide)
        header.addWidget(close_btn)
        root.addLayout(header)

        # Verlauf
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        root.addWidget(self.log, 1)

        # Eingabe
        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Nachricht an MINERVA …  (Enter zum Senden)")
        self.input.returnPressed.connect(self._on_send)
        send = QPushButton("Senden")
        send.clicked.connect(self._on_send)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(send)
        root.addLayout(input_row)

        # Steuerung
        controls = QHBoxLayout()
        self.listen_btn = QPushButton("🎤 Zuhören")
        self.listen_btn.clicked.connect(self.toggle_listen.emit)
        stop_btn = QPushButton("⏹ Stop")
        stop_btn.setObjectName("danger")
        stop_btn.clicked.connect(self.stop_all.emit)
        clear_btn = QPushButton("🗑 Leeren")
        clear_btn.clicked.connect(self._on_clear)
        controls.addWidget(self.listen_btn)
        controls.addWidget(stop_btn)
        controls.addWidget(clear_btn)
        controls.addStretch(1)
        root.addLayout(controls)

    # -- Verlauf-API -------------------------------------------------------
    def _append_html(self, html: str) -> None:
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.insertHtml(html)
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.ensureCursorVisible()

    def append_user(self, text: str) -> None:
        self._close_assistant()
        c = _hex(theme.ACCENT_BRIGHT)
        self._append_html(f'<div style="margin:6px 0;"><b style="color:{c}">Sie ▸ </b>'
                          f'<span style="color:{_hex(theme.TEXT)}">{_esc(text)}</span></div>')

    def append_system(self, text: str) -> None:
        self._close_assistant()
        self._append_html(f'<div style="color:{_hex(theme.MUTED)};font-style:italic;margin:2px 0;">· {_esc(text)}</div>')

    def append_tool(self, text: str) -> None:
        self._close_assistant()
        c = _hex(QColor(120, 130, 255))
        self._append_html(f'<div style="color:{c};margin:2px 0;font-family:monospace;font-size:11px;">⚙ {_esc(text)}</div>')

    def append_error(self, text: str) -> None:
        self._close_assistant()
        self._append_html(f'<div style="color:{_hex(theme.DANGER)};margin:3px 0;">⚠ {_esc(text)}</div>')

    def start_assistant(self) -> None:
        if self._assistant_open:
            return
        c = _hex(theme.OK)
        self._append_html(f'<div style="margin:6px 0;"><b style="color:{c}">MINERVA ▸ </b>'
                          f'<span style="color:{_hex(theme.TEXT)}">')
        self._assistant_open = True

    def append_assistant_token(self, token: str) -> None:
        if not self._assistant_open:
            self.start_assistant()
        # Roh einfügen (kein HTML-Umbruch pro Token, um Performance zu halten).
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.insertPlainText(token)
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.ensureCursorVisible()

    def _close_assistant(self) -> None:
        if self._assistant_open:
            self._append_html("</span></div>")
            self._assistant_open = False

    def end_assistant(self) -> None:
        self._close_assistant()

    # -- Status ------------------------------------------------------------
    def set_title(self, name: str) -> None:
        self.title_label.setText(f"◈ {name.upper()}")

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_listening(self, active: bool) -> None:
        self.listen_btn.setText("🔴 Höre zu…" if active else "🎤 Zuhören")

    # -- intern ------------------------------------------------------------
    def _on_send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.submit_text.emit(text)

    def _on_clear(self) -> None:
        self.log.clear()
        self._assistant_open = False
        self.clear_chat.emit()

    # Ziehen über die Kopfzeile
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 44:
            try:
                wh = self.windowHandle()
                if wh is not None:
                    wh.startSystemMove()
            except Exception:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace("\n", "<br>"))
