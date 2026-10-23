"""Главное окно приложения Fluxus.

Реализует:
- Левый сайдбар с навигацией между страницами (Коннектор, Анализатор, Настройки).
- Правую область со стеком страниц, прогресс-баром и статус-строкой.
- Связь между коннектором и анализатором через сигналы:
  - logs_ready_for_analysis — файлы скачаны на диск, передаются в анализатор.
  - streams_ready_for_analysis — текст из стрима передаётся в анализатор без диска.
- Автопроверку обновлений при старте с индикатором «Новая версия доступна»
  в футере сайдбара.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QProgressBar, QLabel, QFrame, QMessageBox,
)

from config.settings import (
    WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT,
    WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT,
)
from config.config_manager import get_config 
from version import APP_NAME, VERSION 
from gui.widgets.sidebar import Sidebar
from gui.widgets.status_line import StatusLine
from gui.pages.parser_page import ParserPage
from gui.pages.connector_page import ConnectorPage
from gui.pages.settings_page import SettingsPage, UpdateCheckThread
from updater.github_updater import is_update_available 


class MainWindow(QMainWindow):
    """Главное окно со всеми страницами."""

    def __init__(self):
        super().__init__()
        self.config = get_config()

        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        self.resize(WINDOW_DEFAULT_WIDTH, WINDOW_DEFAULT_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        # Восстанавливаем геометрию окна из конфига [1], если была сохранена
        geom = self.config.get("last_window_geometry")
        if geom and isinstance(geom, list) and len(geom) == 4:
            try:
                self.setGeometry(*geom)
            except Exception:
                pass

        self._pages: dict[str, QWidget] = {}

        # Для автопроверки обновлений
        self._latest_release = None
        self._update_check_thread: UpdateCheckThread | None = None

        self._build_ui()

        # Автопроверка обновлений при старте (если включено в config.json [1])
        if self.config.get('auto_check_updates', True):
            QTimer.singleShot(2000, self._start_update_check)

    # ========================================================================
    # Построение UI
    # ========================================================================

    def _build_ui(self):
        central = QWidget()
        central.setObjectName("centralWidget")
        self.setCentralWidget(central)

        root_layout = QHBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ===== Сайдбар слева =====
        self.sidebar = Sidebar()
        self.sidebar.page_changed.connect(self._on_page_changed)
        self.sidebar.update_available_clicked.connect(
            self._on_update_available_clicked
        )
        root_layout.addWidget(self.sidebar)

        # ===== Правая часть: страницы + прогресс + статус-строка =====
        right_container = QWidget()
        right_container.setObjectName("contentArea")
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Стек страниц
        self.stacked = QStackedWidget()
        right_layout.addWidget(self.stacked, stretch=1)

        # Прогресс-бар
        progress_frame = QFrame()
        progress_frame.setStyleSheet(
            "QFrame { background-color: transparent; }"
        )
        progress_layout = QHBoxLayout(progress_frame)
        progress_layout.setContentsMargins(25, 5, 25, 5)
        progress_layout.setSpacing(10)

        self.progress_status_label = QLabel("Готов к работе")
        self.progress_status_label.setStyleSheet(
            "color: #9ca3bf; font-size: 9pt;"
        )
        progress_layout.addWidget(self.progress_status_label, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumWidth(300)
        self.progress_bar.setMaximumWidth(500)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        progress_layout.addWidget(self.progress_bar)
        right_layout.addWidget(progress_frame)

        # Статус-строка (однострочная, с префиксом ">") [15]
        status_frame = QFrame()
        status_frame.setStyleSheet(
            "QFrame { background-color: transparent; }"
        )
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(25, 5, 25, 15)
        status_layout.setSpacing(0)

        self.status_line = StatusLine()
        status_layout.addWidget(self.status_line)
        right_layout.addWidget(status_frame)

        root_layout.addWidget(right_container, stretch=1)

        # Регистрируем все страницы
        self._register_pages()

        # Активируем первую страницу (Коннектор) — открывается при запуске
        self.sidebar.set_active_page("connector")

    def _register_pages(self):
        """Регистрирует все 3 страницы и связывает их сигналы.

        Порядок в сайдбаре:
        1. Коннектор устройств  (открывается при запуске)
        2. Анализатор логов
        3. Настройки
        """
        # ===== Страница 1: Коннектор устройств [20] =====
        self.connector_page = ConnectorPage()
        self.connector_page.log_info.connect(self.status_line.log_info)
        self.connector_page.log_success.connect(self.status_line.log_success)
        self.connector_page.log_warning.connect(self.status_line.log_warning)
        self.connector_page.log_error.connect(self.status_line.log_error)
        # Классическая связь: скачанные файлы → анализатор (через диск)
        self.connector_page.logs_ready_for_analysis.connect(
            self._on_logs_from_connector
        )
        # Стрим-режим: тексты из памяти → анализатор без диска
        self.connector_page.streams_ready_for_analysis.connect(
            self._on_streams_from_connector
        )
        self.sidebar.add_nav_item("connector", "ssh.svg", "Подключение SSH")
        self._pages["connector"] = self.connector_page
        self.stacked.addWidget(self.connector_page)

        # ===== Страница 2: Анализатор логов [21] =====
        self.parser_page = ParserPage()
        self.parser_page.log_info.connect(self.status_line.log_info)
        self.parser_page.log_success.connect(self.status_line.log_success)
        self.parser_page.log_warning.connect(self.status_line.log_warning)
        self.parser_page.log_error.connect(self.status_line.log_error)
        self.parser_page.progress_update.connect(self.set_progress)
        self.sidebar.add_nav_item("parser", "parser.svg", "Анализатор логов")
        self._pages["parser"] = self.parser_page
        self.stacked.addWidget(self.parser_page)

        # ===== Страница 3: Настройки [19] =====
        self.settings_page = SettingsPage()
        self.settings_page.log_info.connect(self.status_line.log_info)
        self.settings_page.log_warning.connect(self.status_line.log_warning)
        self.settings_page.log_error.connect(self.status_line.log_error)
        # Подключаем сигнал ручной проверки обновлений — чтобы она тоже
        # обновляла индикатор в сайдбаре
        if hasattr(self.settings_page, 'update_check_done'):
            self.settings_page.update_check_done.connect(
                self._on_update_check_done
            )
        self.sidebar.add_nav_item("settings", "settings.svg", "Настройки")
        self._pages["settings"] = self.settings_page
        self.stacked.addWidget(self.settings_page)

    # ========================================================================
    # Обработчики сайдбара
    # ========================================================================

    def _on_page_changed(self, page_id: str):
        """Переключает активную страницу по сигналу от сайдбара."""
        page = self._pages.get(page_id)
        if page is not None:
            self.stacked.setCurrentWidget(page)

    def _on_update_available_clicked(self):
        """Пользователь нажал «Новая версия доступна» в сайдбаре [14]."""
        if not self._latest_release:
            return
        release = self._latest_release
        body = release.body or ""
        if len(body) > 400:
            body = body[:400] + "..."
        reply = QMessageBox.question(
            self, "Доступна новая версия",
            f"Доступна версия {release.version}.\n"
            f"Текущая: {VERSION}\n\n"
            f"{body}\n\n"
            f"Скачать обновление?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            # Переходим на страницу настроек [19] — там всё управление
            self.sidebar.set_active_page("settings")
            self.stacked.setCurrentWidget(self.settings_page)
            self.status_line.log_info(
                "Скачивание обновления будет реализовано позже"
            )

    # ========================================================================
    # Проверка обновлений
    # ========================================================================

    def _start_update_check(self):
        """Запускает фоновую проверку обновлений на GitHub [6]."""
        self.status_line.log_info("Автопроверка обновлений...")
        self._update_check_thread = UpdateCheckThread()
        self._update_check_thread.result.connect(self._on_update_check_done)
        self._update_check_thread.start()

    def _on_update_check_done(self, release):
        """Коллбек: проверка обновлений завершена.

        Используется как для автопроверки при старте, так и для ручной
        проверки из страницы настроек [19] (через сигнал update_check_done).
        """
        if release is None:
            # Нет интернета / ошибка — просто скрываем кнопку
            self.sidebar.hide_update_available()
            self.status_line.log_warning(
                "Не удалось проверить обновления"
            )
            return

        if is_update_available(release):
            self._latest_release = release
            self.sidebar.show_update_available(release.version)
            self.status_line.log_info(
                f"Доступна новая версия {release.version}"
            )
        else:
            self._latest_release = None
            self.sidebar.hide_update_available()
            self.status_line.log_info(
                f"Актуальная версия v{VERSION}"
            )

    # ========================================================================
    # Приём данных от коннектора
    # ========================================================================

    def _on_logs_from_connector(self, file_paths: list):
        """
        Срабатывает когда коннектор [20] скачал файлы на диск.
        Добавляем файлы в парсер и автоматически запускаем анализ —
        результат откроется в отдельном окне.
        """
        self.status_line.log_info(
            f"Получено {len(file_paths)} файлов от коннектора"
        )
        # Дедуплицируем — не добавляем файл, который уже в списке
        existing_norm = {str(fp) for fp in self.parser_page.selected_files}
        added = 0
        for fp in file_paths:
            if fp not in existing_norm:
                self.parser_page.selected_files.append(fp)
                existing_norm.add(fp)
                added += 1
        self.parser_page._refresh_files_list()
        self.status_line.log_success(
            f"Добавлено в анализатор: {added} файлов"
        )
        # Переключаемся на страницу парсера, чтобы пользователь видел прогресс
        self.sidebar.set_active_page("parser")
        self.stacked.setCurrentWidget(self.parser_page)
        # Запускаем анализ автоматически — результат откроется в окне
        self.parser_page._on_start_analysis()

    def _on_streams_from_connector(self, device_id: str, streams_data: list,
                                     device_label: str):
        """
        Срабатывает когда коннектор [20] прочитал логи стримом (без диска).
        Передаём стримы в парсер и переключаемся на анализатор.

        Args:
            device_id: ID устройства из коннектора ('001' и т.п.)
            streams_data: список кортежей (filename, text, log_type)
            device_label: читаемая метка устройства ('Lima_mc3_abc' и т.п.)
        """
        self.status_line.log_info(
            f"Получены стримы от коннектора: {device_label} "
            f"({len(streams_data)} файлов)"
        )
        self.parser_page.analyze_from_streams(
            device_id, streams_data, device_label
        )
        # Переключаемся на анализатор — результат уже там
        self.sidebar.set_active_page("parser")
        self.stacked.setCurrentWidget(self.parser_page)

    # ========================================================================
    # Прогресс-бар
    # ========================================================================

    def set_progress(self, value: int, status_text: str = "") -> None:
        """Обновляет прогресс-бар и статусную строку."""
        self.progress_bar.setValue(value)
        if status_text:
            self.progress_status_label.setText(status_text)
        if value >= 100:
            self.progress_status_label.setText("Готово")

    def reset_progress(self) -> None:
        """Сбрасывает прогресс-бар в 0."""
        self.progress_bar.setValue(0)
        self.progress_status_label.setText("Готов к работе")

    # ========================================================================
    # Закрытие окна
    # ========================================================================

    def closeEvent(self, event: QCloseEvent) -> None:
        """Сохраняет геометрию и корректно закрывает активные соединения."""
        # Отключаем все активные воркеры коннектора
        for worker in list(self.connector_page.workers.values()):
            if worker.isRunning():
                worker.request_disconnect()
                worker.wait(3000)
                if worker.isRunning():
                    worker.terminate()
        # Сохраняем геометрию окна в config.json [1]
        try:
            geom = self.geometry()
            self.config.set(
                "last_window_geometry",
                [geom.x(), geom.y(), geom.width(), geom.height()]
            )
            self.config.save()
        except Exception as e:
            print(f"[MainWindow] Ошибка сохранения конфига: {e}")
        super().closeEvent(event)