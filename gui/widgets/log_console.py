"""Консоль логов приложения — для сообщений о прогрессе анализа и операций."""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QTextCharFormat, QColor
from PySide6.QtWidgets import QPlainTextEdit

from gui.theme import DARK_COLORS


class LogConsole(QPlainTextEdit):
    """
    Текстовая консоль с подсветкой уровней логов.
    Используется внизу главного окна для показа прогресса операций.
    """

    MAX_LINES = 2000   # максимум строк в буфере — старые удаляются

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("logConsole")
        self.setReadOnly(True)
        self.setMaximumBlockCount(self.MAX_LINES)
        self.setMinimumHeight(120)
        self.setMaximumHeight(220)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

        # Готовим форматы один раз
        self._fmt_timestamp = QTextCharFormat()
        self._fmt_timestamp.setForeground(QColor(DARK_COLORS["log_timestamp"]))

        self._fmt_info = QTextCharFormat()
        self._fmt_info.setForeground(QColor(DARK_COLORS["log_text"]))

        self._fmt_success = QTextCharFormat()
        self._fmt_success.setForeground(QColor(DARK_COLORS["success"]))

        self._fmt_warning = QTextCharFormat()
        self._fmt_warning.setForeground(QColor(DARK_COLORS["log_warning"]))

        self._fmt_error = QTextCharFormat()
        self._fmt_error.setForeground(QColor(DARK_COLORS["log_error"]))

        # Приветственное сообщение
        self.log_info(f"🚀 Log Parser Superior готов к работе")

    def _append_formatted(self, parts: list[tuple[str, QTextCharFormat]]) -> None:
        """Добавляет строку из форматированных кусков и прокручивает вниз."""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        for text, fmt in parts:
            cursor.setCharFormat(fmt)
            cursor.insertText(text)
        cursor.insertBlock()
        self.setTextCursor(cursor)
        self.ensureCursorVisible()

    def _log(self, prefix: str, text: str, fmt_text: QTextCharFormat) -> None:
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        self._append_formatted([
            (f"{timestamp} ", self._fmt_timestamp),
            (f"{prefix} {text}", fmt_text),
        ])

    def log_info(self, text: str) -> None:
        """Обычное информационное сообщение."""
        self._log("ℹ️", text, self._fmt_info)

    def log_success(self, text: str) -> None:
        """Успешное завершение операции."""
        self._log("✅", text, self._fmt_success)

    def log_warning(self, text: str) -> None:
        """Предупреждение."""
        self._log("⚠️", text, self._fmt_warning)

    def log_error(self, text: str) -> None:
        """Ошибка."""
        self._log("⛔", text, self._fmt_error)

    def clear_log(self) -> None:
        """Полностью очищает консоль."""
        self.clear()
        self.log_info("Консоль очищена")