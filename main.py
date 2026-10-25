"""Точка входа приложения Fluxus."""

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, QTranslator, QLibraryInfo, QLocale

sys.path.insert(0, str(Path(__file__).parent))

from version import APP_NAME, VERSION
from gui.main_window import MainWindow
from gui.theme import apply_dark_theme


def _set_app_icon(app: QApplication) -> None:
    """Устанавливает иконку приложения из logo.png."""
    icon_path = Path(__file__).parent / "logo.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))


def _load_russian_translation(app: QApplication) -> None:
    """Загружает встроенный русский перевод Qt для стандартных диалогов.

    После этого все QMessageBox.Yes/No/Cancel, QFileDialog и прочие
    стандартные элементы Qt будут показывать текст на русском языке.
    """
    translator = QTranslator(app)

    # Путь к встроенным переводам Qt (qtbase_ru.qm и прочие)
    translations_path = QLibraryInfo.path(
        QLibraryInfo.LibraryPath.TranslationsPath
    )

    # Пробуем загрузить qtbase_ru.qm — там кнопки Yes/No/Cancel/OK
    if translator.load("qtbase_ru", translations_path):
        app.installTranslator(translator)
        print(f"[Translation] Loaded qtbase_ru from {translations_path}")
    else:
        # Fallback: старое имя qt_ru.qm (для совместимости со старыми Qt)
        if translator.load("qt_ru", translations_path):
            app.installTranslator(translator)
            print(f"[Translation] Loaded qt_ru (fallback) "
                  f"from {translations_path}")
        else:
            print(f"[Translation] ВНИМАНИЕ: русский перевод Qt не найден "
                  f"в {translations_path}. Кнопки останутся на английском.")

    # Устанавливаем системную локаль как русскую
    # (влияет на форматирование чисел, дат в стандартных виджетах)
    QLocale.setDefault(QLocale(QLocale.Russian, QLocale.Russia))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("Fluxus")

    # Загружаем русский перевод стандартных диалогов Qt
    # ВАЖНО: до создания MainWindow, иначе переводы не применятся к окнам
    _load_russian_translation(app)

    # Иконка приложения (logo.png — квадратный, для окна и панели задач)
    _set_app_icon(app)

    # Применяем тёмную тему
    apply_dark_theme(app)

    # Создаём и показываем главное окно
    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()