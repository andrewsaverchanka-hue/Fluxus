"""Загрузчик SVG-иконок из assets/icons/.

Используется во всех виджетах, которым нужны иконки (сайдбар, кнопки).
Поддерживает перекраску моноцветных SVG через `currentColor` в <svg>.
"""

from pathlib import Path
from functools import lru_cache

from PySide6.QtCore import Qt, QByteArray, QSize
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer


# Папка с SVG-иконками в корне проекта
# gui/icon_loader.py → ../../assets/icons/
ICONS_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"


# ====================================================================
# ЦВЕТА ИКОНОК ДЛЯ РАЗНЫХ СОСТОЯНИЙ
# Привязаны к палитре темы [12] для согласованности.
# ====================================================================

ICON_COLOR_NORMAL = "#9ca3bf"      # обычное состояние (text_muted)
ICON_COLOR_HOVER = "#e4e5f1"       # при наведении (text_primary)
ICON_COLOR_ACTIVE = "#ffffff"      # активная вкладка (белый на индиго-фоне)
ICON_COLOR_ACCENT = "#6366f1"      # акцентный (border_focus)
ICON_COLOR_DANGER = "#ef4444"      # удаление / опасность
ICON_COLOR_SUCCESS = "#10b981"     # успех
ICON_COLOR_WARNING = "#f59e0b"     # предупреждение (жёлтый)
ICON_COLOR_MUTED = "#5b5f7a"       # приглушённый (для placeholder-текстов)  #


# ====================================================================
# ОСНОВНАЯ ФУНКЦИЯ ЗАГРУЗКИ
# ====================================================================

@lru_cache(maxsize=256)
def _load_svg_text(filename: str) -> str | None:
    """Читает SVG-файл с диска. Кэшируется для производительности."""
    path = ICONS_DIR / filename
    if not path.exists():
        print(f"[IconLoader] Файл не найден: {path}")
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[IconLoader] Ошибка чтения {path}: {e}")
        return None


def _render_svg_to_pixmap(svg_text: str, size: int) -> QPixmap:
    """Рендерит SVG-строку в QPixmap заданного размера."""
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    return pixmap


def load_icon(filename: str, size: int = 20,
              color: str = ICON_COLOR_NORMAL) -> QIcon:
    """Загружает SVG-иконку из assets/icons/ и перекрашивает в указанный цвет.

    В SVG-файле цвет должен быть указан как `currentColor` — тогда он будет
    заменён на `color`. Если такой подстроки нет, иконка загрузится как есть.

    Args:
        filename: имя файла (например, "ssh.svg")
        size:     размер иконки в пикселях
        color:    HEX-цвет (например, "#9ca3bf")

    Returns:
        QIcon (пустой, если файл не найден).
    """
    svg_text = _load_svg_text(filename)
    if svg_text is None:
        return QIcon()  # пустая иконка-заглушка
    colored = svg_text.replace("currentColor", color)
    pixmap = _render_svg_to_pixmap(colored, size)
    return QIcon(pixmap)


def load_multi_state_icon(filename: str, size: int = 20) -> QIcon:
    """Загружает SVG и создаёт QIcon с разными состояниями:
    Normal (серый), Active/Selected (белый), Disabled (приглушённый).

    Полезно для кнопок навигации сайдбара [14], где активная вкладка
    должна выделяться цветом иконки.
    """
    svg_text = _load_svg_text(filename)
    if svg_text is None:
        return QIcon()

    icon = QIcon()
    # Обычное состояние
    icon.addPixmap(
        _render_svg_to_pixmap(
            svg_text.replace("currentColor", ICON_COLOR_NORMAL), size
        ),
        QIcon.Normal, QIcon.Off,
    )
    # Hover (On в данном контексте не всегда помогает, но добавим)
    icon.addPixmap(
        _render_svg_to_pixmap(
            svg_text.replace("currentColor", ICON_COLOR_HOVER), size
        ),
        QIcon.Active, QIcon.Off,
    )
    # Активная (чекнутая) вкладка — белая на индиго-фоне
    icon.addPixmap(
        _render_svg_to_pixmap(
            svg_text.replace("currentColor", ICON_COLOR_ACTIVE), size
        ),
        QIcon.Normal, QIcon.On,
    )
    icon.addPixmap(
        _render_svg_to_pixmap(
            svg_text.replace("currentColor", ICON_COLOR_ACTIVE), size
        ),
        QIcon.Active, QIcon.On,
    )
    return icon