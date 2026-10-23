"""Левый сайдбар с навигацией между страницами приложения."""

from pathlib import Path

from PySide6.QtCore import Signal, Qt, QSize
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QButtonGroup, QSizePolicy, QSpacerItem,
)

from config.settings import SIDEBAR_WIDTH
from version import APP_NAME, VERSION
from gui.icon_loader import load_icon, load_multi_state_icon


class Sidebar(QWidget):
    """Левый сайдбар с навигацией.

    Структура:
      - Шапка: единая картинка logo_full.png (лого + текст FLUXUS)
      - "НАВИГАЦИЯ" + кнопки страниц
      - Spacer (растяжка)
      - Футер: кнопка «Новая версия доступна» (скрыта) + копирайт
    """

    page_changed = Signal(str)
    update_available_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 15, 10, 15)
        layout.setSpacing(8)

        # ===== Шапка: единая картинка (лого + текст FLUXUS) =====
        self._build_header(layout)

        # ===== Раздел "НАВИГАЦИЯ" =====
        nav_title = QLabel("НАВИГАЦИЯ")
        nav_title.setObjectName("sidebarSectionTitle")
        layout.addWidget(nav_title)

        # Spacer растягивает всё между навигацией и футером
        layout.addSpacerItem(
            QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        )

        # ===== Футер: кнопка «Новая версия доступна» + копирайт =====
        self.update_available_btn = QPushButton("  Новая версия доступна")
        self.update_available_btn.setObjectName("sidebarUpdateButton")
        self.update_available_btn.setCursor(Qt.PointingHandCursor)
        self.update_available_btn.setIcon(
            load_icon("download.svg", size=16, color="#ffffff")
        )
        self.update_available_btn.setIconSize(QSize(16, 16))
        self.update_available_btn.setStyleSheet("""
            QPushButton#sidebarUpdateButton {
                background-color: #10b981;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 600;
                text-align: left;
            }
            QPushButton#sidebarUpdateButton:hover {
                background-color: #14c993;
            }
            QPushButton#sidebarUpdateButton:pressed {
                background-color: #0ea571;
            }
        """)
        self.update_available_btn.clicked.connect(
            self.update_available_clicked.emit
        )
        self.update_available_btn.hide()
        layout.addWidget(self.update_available_btn)

        footer_label = QLabel(f"v{VERSION} • {APP_NAME}")
        footer_label.setObjectName("sidebarFooter")
        layout.addWidget(footer_label)

    # ------------------------------------------------------------------
    # Шапка сайдбара — одна PNG-картинка (лого + текст)
    # ------------------------------------------------------------------

    def _build_header(self, layout: QVBoxLayout) -> None:
        """Создаёт шапку: единая картинка logo_full.png.

        Это надёжнее, чем рендерить текст шрифтом — картинка выглядит
        одинаково на любой машине, DPI и ОС.
        """
        logo_label = QLabel()
        logo_label.setObjectName("sidebarLogo")
        logo_label.setAlignment(Qt.AlignCenter)

        # Путь к картинке: от gui/widgets/sidebar.py идём на 2 уровня вверх
        logo_path = (Path(__file__).resolve().parent.parent.parent
                     / "logo_full.png")

        if logo_path.exists():
            pixmap = QPixmap(str(logo_path))
            # Масштабируем по ширине сайдбара (с небольшим запасом по бокам)
            target_width = SIDEBAR_WIDTH - 30  # -30px на отступы
            scaled = pixmap.scaledToWidth(
                target_width,
                Qt.SmoothTransformation,
            )
            logo_label.setPixmap(scaled)
        else:
            # Fallback — если logo_full.png не найден
            print(f"[Sidebar] ВНИМАНИЕ: logo_full.png не найден: {logo_path}")
            logo_label.setText(APP_NAME)
            logo_label.setStyleSheet(
                "font-size: 18pt; font-weight: bold; "
                "color: #e4e5f1; padding: 20px;"
            )

        layout.addWidget(logo_label)

    # ------------------------------------------------------------------
    # Публичные методы для навигации
    # ------------------------------------------------------------------

    def add_nav_item(self, page_id: str, icon: str, title: str) -> None:
        """Добавляет пункт навигации.

        Args:
            page_id: уникальный ID страницы.
            icon:    имя SVG-файла (например, "ssh.svg") или эмодзи.
            title:   название пункта.
        """
        if icon.endswith(".svg"):
            btn = QPushButton(f"   {title}")
            btn.setIcon(load_multi_state_icon(icon, size=18))
            btn.setIconSize(QSize(18, 18))
        else:
            btn = QPushButton(f"  {icon}   {title}")

        btn.setObjectName("sidebarNavButton")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.page_changed.emit(page_id))

        self._button_group.addButton(btn)
        self._buttons[page_id] = btn

        layout: QVBoxLayout = self.layout()
        insert_index = self._find_spacer_index(layout)
        layout.insertWidget(insert_index, btn)

    def set_active_page(self, page_id: str) -> None:
        """Программно активирует кнопку страницы."""
        btn = self._buttons.get(page_id)
        if btn:
            btn.setChecked(True)

    # ------------------------------------------------------------------
    # Публичные методы для кнопки «Новая версия доступна»
    # ------------------------------------------------------------------

    def show_update_available(self, version: str = "") -> None:
        """Показывает кнопку «Новая версия доступна» в футере."""
        if version:
            self.update_available_btn.setToolTip(
                f"Доступна версия {version}. Нажмите для подробностей."
            )
        self.update_available_btn.show()

    def hide_update_available(self) -> None:
        """Скрывает кнопку обновления."""
        self.update_available_btn.hide()

    # ------------------------------------------------------------------
    # Утилиты
    # ------------------------------------------------------------------

    @staticmethod
    def _find_spacer_index(layout: QVBoxLayout) -> int:
        """Находит индекс первого Spacer'а в layout."""
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item.spacerItem() is not None:
                return i
        return layout.count()