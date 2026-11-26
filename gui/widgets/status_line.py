"""Однострочная статус-строка приложения в технологичном стиле.

Заменяет многострочную консоль логов. Показывает только последнее
сообщение с префиксом `>`, таймстампом и цветом по уровню (info/success/
warning/error). Шрифт — JetBrains Mono с fallback на Cascadia Mono и
Consolas, чтобы на любой системе выглядело одинаково «технически».

Сигналы совместимы с прежним LogConsole: log_info / log_success /
log_warning / log_error — те же сигнатуры, те же вызовы.
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QLabel

from gui.theme import DARK_COLORS


class StatusLine(QLabel):
    """
    Однострочная статус-строка:
      > [HH:MM:SS]  <текст>

    Цвета по уровню логирования — через HTML-разметку:
      info    → text_primary  (серо-белый)
      success → success       (зелёный)
      warning → warning       (жёлтый/оранжевый)
      error   → danger        (красный)
    Таймстамп всегда зелёный — как «курсор терминала».
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusLine")
        self.setFixedHeight(34)
        self.setTextFormat(Qt.RichText)
        self.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.setContentsMargins(0, 0, 0, 0)

        # Выбираем лучший доступный моноширинный шрифт
        font = self._pick_mono_font()
        self.setFont(font)

        # QSS применим из общего темы, но запишем базовые стили здесь,
        # чтобы виджет корректно выглядел даже без общего theme.qss
        self.setStyleSheet(f"""
            QLabel#statusLine {{
                background-color: {DARK_COLORS['log_bg']};
                color: {DARK_COLORS['log_text']};
                border: 1px solid {DARK_COLORS['border']};
                border-radius: 6px;
                padding: 4px 12px;
                letter-spacing: 0.5px;
            }}
        """)

        # Приветственное сообщение
        self.log_info("🚀 Log Parser Superior готов к работе")

    # ------------------------------------------------------------------
    # Выбор шрифта: пробуем JetBrains Mono → Cascadia Mono → Consolas → Courier New
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_mono_font() -> QFont:
        """Выбирает лучший доступный моноширинный шрифт в системе."""
        preferred = [
            "JetBrains Mono",     # идеальный техно-вид, нужно установить вручную
            "Cascadia Mono",      # Windows 11 — поставляется в комплекте
            "Cascadia Code",      # Windows 10 с терминалом Microsoft
            "Fira Code",          # ещё один техно-шрифт с лигатурами
            "Consolas",           # есть на всех Windows
            "Courier New",        # универсальный fallback
        ]
        installed = set(QFontDatabase.families())
        chosen = None
        for name in preferred:
            if name in installed:
                chosen = name
                break
        if chosen is None:
            # Крайний случай — берём системный моноширинный
            font = QFont()
            font.setStyleHint(QFont.Monospace)
            font.setPointSize(10)
            return font

        font = QFont(chosen)
        font.setPointSize(10)
        # Для техно-эффекта можно добавить лёгкую регулировку
        font.setStyleStrategy(QFont.PreferAntialias)
        return font

    # ------------------------------------------------------------------
    # API совместимо с LogConsole: те же методы, те же сигнатуры
    # ------------------------------------------------------------------

    def log_info(self, text: str) -> None:
        """Обычное информационное сообщение."""
        self._set_message(text, DARK_COLORS["text_primary"])

    def log_success(self, text: str) -> None:
        """Успешное завершение операции."""
        self._set_message(text, DARK_COLORS["success"])

    def log_warning(self, text: str) -> None:
        """Предупреждение."""
        self._set_message(text, DARK_COLORS["warning"])

    def log_error(self, text: str) -> None:
        """Ошибка."""
        self._set_message(text, DARK_COLORS["danger"])

    def clear_log(self) -> None:
        """Сбрасывает статус-строку в приветственное сообщение."""
        self.log_info("🚀 Log Parser Superior готов к работе")

    # ------------------------------------------------------------------
    # Внутренняя логика
    # ------------------------------------------------------------------

    def _set_message(self, text: str, color_text: str) -> None:
        """Собирает HTML с цветным таймстампом и сообщением, обновляет QLabel."""
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        timestamp_color = DARK_COLORS["log_timestamp"]
        prompt_color = DARK_COLORS["accent"]

        # Экранируем HTML-символы в тексте, чтобы случайные &, <, > не ломали рендер
        safe_text = (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

        html = (
            f'<span style="color: {prompt_color}; font-weight: 600;">&gt;</span> '
            f'<span style="color: {timestamp_color};">{timestamp}</span> '
            f'<span style="color: {color_text};">{safe_text}</span>'
        )
        self.setText(html)
        # Полная подсказка — на случай длинного сообщения, которое не поместится
        self.setToolTip(f"{timestamp} {text}")