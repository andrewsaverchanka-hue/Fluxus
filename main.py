"""Точка входа приложения Fluxus."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

sys.path.insert(0, str(Path(__file__).parent))

from version import APP_NAME, VERSION
from gui.main_window import MainWindow
from gui.theme import apply_dark_theme


def _set_app_icon(app: QApplication) -> None:
    """Устанавливает иконку приложения из logo.png."""
    icon_path = Path(__file__).parent / "logo.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("Fluxus")

    # Иконка приложения (logo.png — квадратный, для окна и панели задач)
    _set_app_icon(app)

    # Применяем тёмную тему [12]
    apply_dark_theme(app)

    # Создаём и показываем главное окно
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()