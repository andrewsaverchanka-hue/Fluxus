"""Загрузочное окно Fluxus.

Безрамочное, со скруглёнными углами, в палитре приложения: только логотип
и полоса загрузки — больше ничего.
"""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget,
)

BG = "#1a1b2e"
BORDER = "#34354e"
TRACK = "#2d2e45"
ACCENT = "#6366f1"


class SplashScreen(QWidget):
    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SplashScreen,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(520, 340)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("splashCard")
        card.setStyleSheet(
            f"QFrame#splashCard {{ background: {BG};"
            f" border: 1px solid {BORDER}; border-radius: 18px; }}"
        )
        outer.addWidget(card)

        lay = QVBoxLayout(card)
        lay.setContentsMargins(36, 32, 36, 30)
        lay.setSpacing(22)

        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        path = Path(__file__).resolve().parent.parent / "logo_full.png"
        pix = QPixmap(str(path)) if path.exists() else QPixmap()
        if not pix.isNull():
            logo.setPixmap(
                pix.scaled(420, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        lay.addStretch(1)
        lay.addWidget(logo)
        lay.addStretch(1)

        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {TRACK}; border: none;"
            " border-radius: 4px; }"
            f"QProgressBar::chunk {{ background: {ACCENT};"
            " border-radius: 4px; }"
        )
        lay.addWidget(bar)

        self._bar = bar
        self._value = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(90)

        self._center()

    def _tick(self):
        # Плавный «прогресс» до 90 %, дальше ждём готовности приложения.
        if self._value >= 90:
            return
        self._value += 1 if self._value < 60 else 0.4
        self._bar.setValue(int(self._value))

    def _center(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.center() - self.rect().center())

    def set_progress(self, value: int):
        self._value = max(0, min(100, int(value)))
        self._bar.setValue(self._value)

    def finish(self):
        self._timer.stop()
        self._bar.setValue(100)
        QTimer.singleShot(260, self.close)
