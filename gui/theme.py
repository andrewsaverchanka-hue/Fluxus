"""QSS-стили приложения: тёмная тема с исправленным рендером эмодзи."""

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette, QColor


# =============================================================================
# ПАЛИТРА ЦВЕТОВ
# =============================================================================

DARK_COLORS = {
    # Фоны (от самого тёмного к светлому)
    "bg_window":       "#1a1b2e",   # основной фон окна
    "bg_panel":        "#232438",   # фон панелей (сайдбар, карточки)
    "bg_elevated":     "#2a2b40",   # фон приподнятых элементов (поля ввода, hover)
    "bg_input":        "#2d2e45",   # фон полей ввода
    "bg_hover":        "#353657",   # hover-состояние
    "bg_pressed":      "#2a2b40",

    # Акценты (фиолетовый градиент)
    "accent":          "#6366f1",   # основной акцент (кнопки, активные пункты)
    "accent_hover":    "#7c7fe8",
    "accent_pressed":  "#4f52d4",
    "accent_light":    "#8b5cf6",   # вторичный фиолетовый

    # Текст
    "text_primary":    "#e4e5f1",   # основной текст
    "text_secondary":  "#9ca3bf",   # вторичный текст (подписи, placeholder)
    "text_disabled":   "#5b5f7a",
    "text_on_accent":  "#ffffff",   # текст на акцентных кнопках

    # Статусы
    "success":         "#10b981",
    "warning":         "#f59e0b",
    "danger":          "#ef4444",
    "info":            "#3b82f6",

    # Границы и разделители
    "border":          "#34354e",
    "border_focus":    "#6366f1",
    "divider":         "#2d2e45",

    # Консоль логов (используется в StatusLine)
    "log_bg":          "#141524",
    "log_timestamp":   "#4ade80",
    "log_text":        "#c9cad8",
    "log_error":       "#f87171",
    "log_warning":     "#fbbf24",
}


# Глобальный font-family с fallback на эмодзи-шрифты для корректного рендера
# иконок во всех виджетах приложения
FONT_FAMILY = (
    '"Segoe UI", "Inter", "Arial", '
    '"Segoe UI Emoji", "Noto Color Emoji", '
    '"Apple Color Emoji", sans-serif'
)


# =============================================================================
# ОСНОВНОЙ QSS ТЁМНОЙ ТЕМЫ
# =============================================================================

def _build_dark_qss() -> str:
    c = DARK_COLORS
    return f"""
    /* ===== Главное окно ===== */
    QMainWindow, QWidget#centralWidget {{
        background-color: {c['bg_window']};
        color: {c['text_primary']};
    }}

    /* Глобальный шрифт с поддержкой эмодзи — применяется ко всем виджетам */
    QWidget {{
        color: {c['text_primary']};
        font-family: {FONT_FAMILY};
        font-size: 10pt;
    }}

    /* ===== Сайдбар ===== */
    QWidget#sidebar {{
        background-color: {c['bg_panel']};
        border-right: 1px solid {c['border']};
    }}

    QLabel#sidebarLogo {{
        padding: 20px 0px 10px 0px;
        qproperty-alignment: AlignCenter;
    }}

    QLabel#sidebarAppName {{
        color: {c['text_primary']};
        font-size: 11pt;
        font-weight: 600;
        padding: 5px 20px 0px 20px;
    }}

    QLabel#sidebarAppVersion {{
        color: {c['text_secondary']};
        font-size: 8pt;
        padding: 0px 20px 20px 20px;
    }}

    QLabel#sidebarSectionTitle {{
        color: {c['text_secondary']};
        font-size: 8pt;
        font-weight: 600;
        letter-spacing: 1px;
        padding: 15px 20px 8px 20px;
    }}

    QLabel#sidebarFooter {{
        color: {c['text_disabled']};
        font-size: 8pt;
        qproperty-alignment: AlignCenter;
        padding: 10px;
    }}

    QPushButton#sidebarNavButton {{
        background-color: transparent;
        color: {c['text_primary']};
        border: none;
        border-radius: 8px;
        padding: 10px 15px;
        margin: 2px 10px;
        text-align: left;
        font-size: 10pt;
        min-height: 24px;
    }}

    QPushButton#sidebarNavButton:hover {{
        background-color: {c['bg_hover']};
    }}

    QPushButton#sidebarNavButton:checked {{
        background-color: {c['accent']};
        color: {c['text_on_accent']};
        font-weight: 600;
    }}

    QPushButton#sidebarNavButton:checked:hover {{
        background-color: {c['accent_hover']};
    }}

    QPushButton#sidebarActionButton {{
        background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 8px 12px;
        margin: 3px 10px;
        font-size: 9pt;
        min-height: 22px;
    }}

    QPushButton#sidebarActionButton:hover {{
        background-color: {c['bg_hover']};
        border-color: {c['accent']};
    }}

    QPushButton#sidebarActionButton:pressed {{
        background-color: {c['bg_pressed']};
    }}

    /* ===== Основная рабочая область ===== */
    QWidget#contentArea {{
        background-color: {c['bg_window']};
    }}

    QLabel#pageTitle {{
        color: {c['text_primary']};
        font-size: 20pt;
        font-weight: 700;
        padding: 20px 25px 10px 25px;
    }}

    QLabel#pageSubtitle {{
        color: {c['text_secondary']};
        font-size: 10pt;
        padding: 0px 25px 15px 25px;
    }}

    /* ===== Карточки / контейнеры ===== */
    QFrame#card {{
        background-color: {c['bg_panel']};
        border: 1px solid {c['border']};
        border-radius: 12px;
        padding: 15px;
    }}

    /* ===== Крупные primary / secondary кнопки ===== */
    QPushButton#primaryButton {{
        background-color: {c['accent']};
        color: {c['text_on_accent']};
        border: none;
        border-radius: 10px;
        padding: 12px 20px;
        font-size: 11pt;
        font-weight: 600;
        min-height: 28px;
    }}

    QPushButton#primaryButton:hover {{
        background-color: {c['accent_hover']};
    }}

    QPushButton#primaryButton:pressed {{
        background-color: {c['accent_pressed']};
    }}

    QPushButton#primaryButton:disabled {{
        background-color: {c['bg_elevated']};
        color: {c['text_disabled']};
    }}

    QPushButton#secondaryButton {{
        background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        padding: 10px 16px;
        font-size: 10pt;
        min-height: 26px;
    }}

    QPushButton#secondaryButton:hover {{
        background-color: {c['bg_hover']};
        border-color: {c['accent']};
    }}

    QPushButton#secondaryButton:pressed {{
        background-color: {c['bg_pressed']};
    }}

    QPushButton#secondaryButton:disabled {{
        background-color: {c['bg_panel']};
        color: {c['text_disabled']};
        border-color: {c['border']};
    }}

    /* ===== Компактные кнопки — скругления 10px ===== */
    QPushButton#compactButton {{
        background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 10px;
        padding: 7px 14px;
        font-size: 9pt;
        font-weight: 500;
        min-height: 24px;
        max-height: 34px;
    }}

    QPushButton#compactButton:hover {{
        background-color: {c['bg_hover']};
        border-color: {c['accent']};
    }}

    QPushButton#compactButton:pressed {{
        background-color: {c['bg_pressed']};
    }}

    QPushButton#compactButton:disabled {{
        color: {c['text_disabled']};
        background-color: {c['bg_panel']};
    }}

    QPushButton#compactPrimaryButton {{
        background-color: {c['accent']};
        color: {c['text_on_accent']};
        border: none;
        border-radius: 10px;
        padding: 7px 16px;
        font-size: 9pt;
        font-weight: 600;
        min-height: 24px;
        max-height: 34px;
    }}

    QPushButton#compactPrimaryButton:hover {{
        background-color: {c['accent_hover']};
    }}

    QPushButton#compactPrimaryButton:pressed {{
        background-color: {c['accent_pressed']};
    }}

    QPushButton#compactPrimaryButton:disabled {{
        background-color: {c['bg_elevated']};
        color: {c['text_disabled']};
    }}

    /* ===== Обычные кнопки по умолчанию ===== */
    QPushButton {{
        background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 6px 12px;
        font-size: 9pt;
        min-height: 22px;
    }}

    QPushButton:hover {{
        background-color: {c['bg_hover']};
        border-color: {c['accent']};
    }}

    QPushButton:pressed {{
        background-color: {c['bg_pressed']};
    }}

    QPushButton:disabled {{
        color: {c['text_disabled']};
        background-color: {c['bg_panel']};
    }}

    /* ===== Поля ввода ===== */
    QLineEdit, QTextEdit, QPlainTextEdit {{
        background-color: {c['bg_input']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 8px 12px;
        selection-background-color: {c['accent']};
        selection-color: {c['text_on_accent']};
        min-height: 24px;
    }}

    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border-color: {c['border_focus']};
    }}

    QLineEdit:disabled {{
        background-color: {c['bg_panel']};
        color: {c['text_disabled']};
    }}

    /* ===== ComboBox ===== */
    QComboBox {{
        background-color: {c['bg_input']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 6px 12px;
        min-height: 26px;
    }}

    QComboBox:hover {{
        border-color: {c['accent']};
    }}

    QComboBox::drop-down {{
        border: none;
        width: 25px;
    }}

    QComboBox QAbstractItemView {{
        background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        selection-background-color: {c['accent']};
        selection-color: {c['text_on_accent']};
        padding: 4px;
    }}

    /* ===== QSpinBox без стрелок +/- ===== */
    QSpinBox {{
        background-color: {c['bg_input']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 6px 10px;
        min-height: 26px;
    }}

    QSpinBox:focus {{
        border-color: {c['border_focus']};
    }}

    QSpinBox::up-button, QSpinBox::down-button {{
        width: 0;
        height: 0;
        border: none;
    }}

    /* ===== QDateEdit — компактное поле выбора даты =====
       Календарь открывается через ClickableDateEdit в коде (клик по полю).
       Все встроенные кнопки (стрелка дроп-дауна и невидимые +/-) полностью
       скрыты, чтобы при фокусе не появлялась пустая область справа. */
    QDateEdit {{
        background-color: {c['bg_input']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 6px 12px;
        min-height: 26px;
    }}

    QDateEdit:hover {{
        border-color: {c['accent']};
    }}

    QDateEdit:focus {{
        border-color: {c['border_focus']};
    }}

    QDateEdit::drop-down {{
        width: 0;
        border: none;
    }}

    QDateEdit::down-arrow {{
        width: 0;
        height: 0;
    }}

    /* QDateEdit наследует QAbstractSpinBox, у которого есть up-button
       и down-button. Их тоже надо скрыть, иначе при фокусе справа
       появляется пустая область с невидимыми кнопками */
    QDateEdit::up-button, QDateEdit::down-button {{
        width: 0;
        height: 0;
        border: none;
        subcontrol-origin: padding;
        subcontrol-position: center right;
    }}

    /* ===== QCalendarWidget — всплывающий календарь ===== */
    QCalendarWidget {{
        background-color: {c['bg_panel']};
        color: {c['text_primary']};
    }}

    /* Шапка (месяц + год + стрелки) */
    QCalendarWidget QWidget#qt_calendar_navigationbar {{
        background-color: {c['bg_elevated']};
        border: none;
        min-height: 36px;
    }}

    QCalendarWidget QToolButton {{
        background-color: transparent;
        color: {c['text_primary']};
        border: none;
        border-radius: 6px;
        padding: 4px 10px;
        margin: 3px;
        font-weight: 600;
        font-size: 10pt;
    }}

    QCalendarWidget QToolButton:hover {{
        background-color: {c['bg_hover']};
    }}

    QCalendarWidget QToolButton:pressed {{
        background-color: {c['accent']};
    }}

    QCalendarWidget QToolButton::menu-indicator {{
        image: none;
    }}

    /* Выпадающие меню (выбор года и месяца) */
    QCalendarWidget QMenu {{
        background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 6px;
    }}

    QCalendarWidget QMenu::item {{
        padding: 6px 20px;
    }}

    QCalendarWidget QMenu::item:selected {{
        background-color: {c['accent']};
        color: {c['text_on_accent']};
    }}

    QCalendarWidget QSpinBox {{
        background-color: {c['bg_input']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 4px;
        padding: 2px 6px;
    }}

    /* Основная сетка дат */
    QCalendarWidget QAbstractItemView {{
        background-color: {c['bg_panel']};
        color: {c['text_primary']};
        selection-background-color: {c['accent']};
        selection-color: {c['text_on_accent']};
        outline: none;
    }}

    /* Выключенные (соседних месяцев) */
    QCalendarWidget QAbstractItemView:disabled {{
        color: {c['text_disabled']};
    }}

    QCalendarWidget QTableView {{
        alternate-background-color: {c['bg_panel']};
    }}

    /* ===== Progress bar ===== */
    QProgressBar {{
        background-color: {c['bg_elevated']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        text-align: center;
        color: {c['text_primary']};
        font-size: 9pt;
        height: 22px;
    }}

    QProgressBar::chunk {{
        background-color: {c['accent']};
        border-radius: 5px;
    }}

    /* ===== Scrollbars ===== */
    QScrollBar:vertical {{
        background-color: {c['bg_window']};
        width: 12px;
        margin: 0;
        border: none;
    }}

    QScrollBar::handle:vertical {{
        background-color: {c['bg_hover']};
        border-radius: 6px;
        min-height: 30px;
    }}

    QScrollBar::handle:vertical:hover {{
        background-color: {c['accent']};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
        background: none;
    }}

    QScrollBar:horizontal {{
        background-color: {c['bg_window']};
        height: 12px;
        margin: 0;
        border: none;
    }}

    QScrollBar::handle:horizontal {{
        background-color: {c['bg_hover']};
        border-radius: 6px;
        min-width: 30px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background-color: {c['accent']};
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
        background: none;
    }}

    /* ===== Консоль логов (QPlainTextEdit, если ещё где-то используется) ===== */
    QPlainTextEdit#logConsole, QTextEdit#logConsole {{
        background-color: {c['log_bg']};
        color: {c['log_text']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 10px;
        font-family: "Consolas", "Courier New", monospace;
        font-size: 9pt;
    }}

    /* ===== Статус-строка (однострочная, с префиксом ">") ===== */
    QLabel#statusLine {{
        background-color: {c['log_bg']};
        color: {c['log_text']};
        border: 1px solid {c['border']};
        border-radius: 8px;
        padding: 4px 12px;
        letter-spacing: 0.5px;
    }}

    /* ===== Статусбар ===== */
    QStatusBar {{
        background-color: {c['bg_panel']};
        color: {c['text_secondary']};
        border-top: 1px solid {c['border']};
    }}

    /* ===== Checkbox ===== */
    QCheckBox {{
        color: {c['text_primary']};
        spacing: 8px;
        min-height: 22px;
    }}

    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c['border']};
        border-radius: 4px;
        background-color: {c['bg_input']};
    }}

    QCheckBox::indicator:checked {{
        background-color: {c['accent']};
        border-color: {c['accent']};
    }}

    QCheckBox::indicator:hover {{
        border-color: {c['accent']};
    }}

    /* ===== RadioButton ===== */
    QRadioButton {{
        color: {c['text_primary']};
        spacing: 8px;
        min-height: 22px;
    }}

    QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {c['border']};
        border-radius: 8px;
        background-color: {c['bg_input']};
    }}

    QRadioButton::indicator:checked {{
        background-color: {c['accent']};
        border-color: {c['accent']};
    }}

    QRadioButton::indicator:hover {{
        border-color: {c['accent']};
    }}

    /* ===== Tooltips ===== */
    QToolTip {{
        background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        border: 1px solid {c['accent']};
        border-radius: 4px;
        padding: 5px 8px;
    }}

    /* ===== QTabWidget — внутренние вкладки на страницах ===== */
    QTabWidget::pane {{
        background-color: {c['bg_panel']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        top: -1px;
    }}

    /* В окнах с результатами (QMainWindow верхнего уровня) убираем
       рамку/фон под вкладками — выглядит чище */
    QMainWindow QTabWidget::pane {{
        background-color: transparent;
        border: none;
        border-radius: 0;
    }}

    QTabBar::tab {{
        background-color: {c['bg_elevated']};
        color: {c['text_secondary']};
        padding: 8px 14px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        margin-right: 2px;
        min-height: 22px;
    }}

    QTabBar::tab:selected {{
        background-color: {c['bg_panel']};
        color: {c['text_primary']};
        border-top: 2px solid {c['accent']};
        font-weight: 600;
    }}

    QTabBar::tab:hover {{
        background-color: {c['bg_hover']};
    }}

    /* ===== QTreeWidget / QTableWidget ===== */
    QTreeWidget, QTableWidget, QTreeView, QTableView {{
        background-color: {c['bg_panel']};
        alternate-background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        gridline-color: {c['divider']};
        selection-background-color: {c['accent']};
        selection-color: {c['text_on_accent']};
    }}

    QTreeWidget::item, QTableWidget::item {{
        padding: 6px;
        border: none;
    }}

    QTreeWidget::item:hover, QTableWidget::item:hover {{
        background-color: {c['bg_hover']};
    }}

    QTreeWidget::item:selected, QTableWidget::item:selected {{
        background-color: {c['accent']};
        color: {c['text_on_accent']};
    }}

    QHeaderView::section {{
        background-color: {c['bg_elevated']};
        color: {c['text_primary']};
        padding: 8px;
        border: none;
        border-right: 1px solid {c['divider']};
        border-bottom: 1px solid {c['border']};
        font-weight: 600;
    }}

    /* ===== QListWidget ===== */
    QListWidget {{
        background-color: {c['bg_input']};
        color: {c['text_primary']};
        border: 1px solid {c['border']};
        border-radius: 6px;
        padding: 4px;
    }}

    QListWidget::item {{
        padding: 4px 8px;
        border-radius: 3px;
        min-height: 20px;
    }}

    QListWidget::item:selected {{
        background-color: {c['accent']};
        color: {c['text_on_accent']};
    }}

    QListWidget::item:hover {{
        background-color: {c['bg_hover']};
    }}

    /* ===== Labels ===== */
    QLabel {{
        color: {c['text_primary']};
        background-color: transparent;
    }}
    """


def apply_dark_theme(app: QApplication) -> None:
    """Применяет тёмную тему к приложению."""
    # Устанавливаем базовую палитру (для нативных диалогов)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(DARK_COLORS["bg_window"]))
    palette.setColor(QPalette.WindowText, QColor(DARK_COLORS["text_primary"]))
    palette.setColor(QPalette.Base, QColor(DARK_COLORS["bg_input"]))
    palette.setColor(QPalette.AlternateBase, QColor(DARK_COLORS["bg_elevated"]))
    palette.setColor(QPalette.ToolTipBase, QColor(DARK_COLORS["bg_elevated"]))
    palette.setColor(QPalette.ToolTipText, QColor(DARK_COLORS["text_primary"]))
    palette.setColor(QPalette.Text, QColor(DARK_COLORS["text_primary"]))
    palette.setColor(QPalette.Button, QColor(DARK_COLORS["bg_elevated"]))
    palette.setColor(QPalette.ButtonText, QColor(DARK_COLORS["text_primary"]))
    palette.setColor(QPalette.BrightText, QColor(DARK_COLORS["text_on_accent"]))
    palette.setColor(QPalette.Link, QColor(DARK_COLORS["accent"]))
    palette.setColor(QPalette.Highlight, QColor(DARK_COLORS["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor(DARK_COLORS["text_on_accent"]))
    app.setPalette(palette)

    # Применяем QSS
    app.setStyleSheet(_build_dark_qss())