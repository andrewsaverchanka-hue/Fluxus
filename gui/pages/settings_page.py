"""Страница настроек приложения."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QFrame, QCheckBox, QMessageBox, QProgressDialog
)

from config.config_manager import get_config
from version import VERSION, APP_NAME
from updater.github_updater import (
    check_latest_release, is_update_available,
)
from gui.icon_loader import (
    load_icon,
    ICON_COLOR_NORMAL,
    ICON_COLOR_HOVER,
    ICON_COLOR_WARNING,
    ICON_COLOR_SUCCESS,
)

from updater.github_updater import (
    check_latest_release, is_update_available,
    download_update, install_update, is_frozen,   # ← новые
)

class UpdateCheckThread(QThread):
    """Проверка обновлений в фоне."""
    result = Signal(object)

    def run(self):
        release = check_latest_release()
        self.result.emit(release)

class UpdateDownloadThread(QThread):
    """Фоновое скачивание обновления с сигналами прогресса."""

    progress = Signal(int, int)      # (bytes_done, total_bytes)
    finished_download = Signal(object)   # Path или None (ошибка/отмена)

    def __init__(self, release, parent=None):
        super().__init__(parent)
        self.release = release
        self._cancel = False

    def cancel(self):
        """Запрашивает отмену скачивания из UI-потока."""
        self._cancel = True

    def run(self):
        result = download_update(
            self.release,
            progress_callback=lambda done, total: self.progress.emit(done, total),
            cancel_check=lambda: self._cancel,
        )
        self.finished_download.emit(result)

class SettingsPage(QWidget):
    """Страница настроек: SSH-ключ, автообновления."""

    log_info = Signal(str)
    log_warning = Signal(str)
    log_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_config()
        self._update_thread: UpdateCheckThread | None = None
        self._build_ui()
        self._load_from_config()
    def _start_download(self, release):
        """Запускает скачивание обновления с прогресс-диалогом."""
        # Проверка dev-режима
        if not is_frozen():
            QMessageBox.information(
                self,
                "Dev-режим",
                "Обновления доступны только для собранной .exe-версии.\n\n"
                "Сейчас вы запустили приложение через python main.py.\n"
                "Для получения обновлений используйте Fluxus.exe."
            )
            return

        # Прогресс-диалог
        self._progress_dialog = QProgressDialog(
            f"Скачивание {release.asset_name}...",
            "Отмена",
            0, 100,
            self
        )
        self._progress_dialog.setWindowTitle("Обновление Fluxus")
        self._progress_dialog.setWindowModality(Qt.WindowModal)
        self._progress_dialog.setMinimumDuration(0)
        self._progress_dialog.setValue(0)

        # Фоновый поток скачивания
        self._download_thread = UpdateDownloadThread(release, parent=self)
        self._download_thread.progress.connect(self._on_download_progress)
        self._download_thread.finished_download.connect(
            self._on_download_finished
        )

        # Отмена из диалога → отмена потока
        self._progress_dialog.canceled.connect(self._download_thread.cancel)

        self.log_info.emit(f"Начало скачивания {release.asset_name}...")
        self._download_thread.start()

    def _on_download_progress(self, done, total):
        """Обновление прогресс-бара."""
        if total > 0:
            percent = int((done / total) * 100)
            self._progress_dialog.setValue(percent)
            mb_done = done / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self._progress_dialog.setLabelText(
                f"Скачивание... {mb_done:.1f} / {mb_total:.1f} МБ"
            )

    def _on_download_finished(self, result_path):
        """Коллбек: скачивание завершено."""
        self._progress_dialog.close()

        if result_path is None:
            # Либо отмена, либо ошибка
            self.log_warning.emit("Скачивание отменено или произошла ошибка")
            self._set_status(
                "warning.svg",
                "Скачивание не завершено. Попробуйте снова.",
                color=ICON_COLOR_WARNING,
            )
            return

        # Успешно скачано — предлагаем установить
        self.log_info.emit(f"Скачано: {result_path.name}")
        reply = QMessageBox.question(
            self,
            "Установить обновление?",
            f"Обновление скачано:\n{result_path}\n\n"
            f"Приложение закроется и запустится заново с новой версией.\n\n"
            f"Установить сейчас?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            self.log_info.emit(
                f"Установка отложена. Файл сохранён: {result_path}"
            )
            return

        # Запускаем установку
        if install_update(result_path):
            self.log_info.emit("Установка запущена. Приложение закроется...")
            # Закрываем приложение — bat-скрипт заменит .exe и запустит новый
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()
        else:
            QMessageBox.critical(
                self,
                "Ошибка установки",
                "Не удалось запустить инсталлятор.\n"
                f"Файл обновления сохранён:\n{result_path}\n\n"
                f"Попробуйте запустить его вручную."
            )
    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(15)

        # ===== Заголовок страницы с SVG-иконкой =====
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(10)
        title_row.setAlignment(Qt.AlignVCenter)

        title_icon = QLabel()
        title_icon.setFixedSize(28, 28)
        title_icon.setPixmap(
            load_icon("settings.svg", size=28,
                      color=ICON_COLOR_HOVER).pixmap(28, 28)
        )
        title_row.addWidget(title_icon, 0, Qt.AlignVCenter)

        title = QLabel("Настройки")
        title.setObjectName("pageTitle")
        title.setStyleSheet("QLabel#pageTitle { padding: 0px; }")
        title_row.addWidget(title, 0, Qt.AlignVCenter)
        title_row.addStretch()

        layout.addLayout(title_row)

        subtitle = QLabel(
            "SSH-ключ для подключения к устройствам, проверка обновлений."
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # ===== Карточка 1: SSH-ключ =====
        ssh_card = self._build_card("key.svg",
                                     "SSH-ключ для подключения к устройствам")
        ssh_layout = ssh_card.layout()

        key_row = QHBoxLayout()
        key_row.addWidget(QLabel("Путь к ключу:"))
        self.key_path_edit = QLineEdit()
        self.key_path_edit.setReadOnly(True)
        self.key_path_edit.setPlaceholderText("Ключ не выбран")
        key_row.addWidget(self.key_path_edit, stretch=1)

        browse_btn = QPushButton("  Обзор...")
        browse_btn.setIcon(load_icon("folder.svg", size=16))
        browse_btn.setIconSize(QSize(16, 16))
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self._on_browse_key)
        key_row.addWidget(browse_btn)
        ssh_layout.addLayout(key_row)

        pwd_row = QHBoxLayout()
        pwd_row.addWidget(QLabel("Пароль (fallback):"))
        self.default_password_edit = QLineEdit()
        self.default_password_edit.setEchoMode(QLineEdit.Password)
        self.default_password_edit.setPlaceholderText(
            "Используется, если на устройстве не настроен SSH-ключ"
        )
        self.default_password_edit.textChanged.connect(
            lambda text: self.config.set('default_device_password', text)
        )
        pwd_row.addWidget(self.default_password_edit, stretch=1)
        ssh_layout.addLayout(pwd_row)

        save_pwd_btn = QPushButton("  Сохранить")
        save_pwd_btn.setIcon(load_icon("save.svg", size=16))
        save_pwd_btn.setIconSize(QSize(16, 16))
        save_pwd_btn.setCursor(Qt.PointingHandCursor)
        save_pwd_btn.clicked.connect(self._save_config)
        ssh_layout.addWidget(save_pwd_btn)

        layout.addWidget(ssh_card)

        # ===== Карточка 2: Обновления =====
        updates_card = self._build_card("refresh.svg", "Обновления")
        updates_layout = updates_card.layout()

        updates_layout.addWidget(QLabel(
            f"Текущая версия: {APP_NAME} v{VERSION}"
        ))

        self.auto_check_checkbox = QCheckBox(
            "Проверять обновления при запуске"
        )
        self.auto_check_checkbox.stateChanged.connect(
            lambda state: self.config.set_and_save(
                'auto_check_updates', state == Qt.Checked.value
            )
        )
        updates_layout.addWidget(self.auto_check_checkbox)

        check_row = QHBoxLayout()
        self.check_updates_btn = QPushButton("  Проверить обновления сейчас")
        self.check_updates_btn.setObjectName("primaryButton")
        self.check_updates_btn.setIcon(load_icon("refresh.svg", size=16))
        self.check_updates_btn.setIconSize(QSize(16, 16))
        self.check_updates_btn.setCursor(Qt.PointingHandCursor)
        self.check_updates_btn.clicked.connect(self._on_check_updates)
        check_row.addWidget(self.check_updates_btn)
        check_row.addStretch()
        updates_layout.addLayout(check_row)

        # ===== Статус обновлений — виджет с иконкой и текстом =====
        self.update_status_widget = QWidget()
        update_status_layout = QHBoxLayout(self.update_status_widget)
        update_status_layout.setContentsMargins(0, 0, 0, 0)
        update_status_layout.setSpacing(6)
        update_status_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.update_status_icon = QLabel()
        self.update_status_icon.setFixedSize(16, 16)
        self.update_status_icon.hide()  # скрыта, пока нет статуса
        update_status_layout.addWidget(self.update_status_icon)

        self.update_status_label = QLabel("")
        self.update_status_label.setWordWrap(True)
        self.update_status_label.setStyleSheet("color: #9ca3bf;")
        update_status_layout.addWidget(self.update_status_label, stretch=1)

        updates_layout.addWidget(self.update_status_widget)

        layout.addWidget(updates_card)
        layout.addStretch()

    # ------------------------------------------------------------------
    # Утилиты UI
    # ------------------------------------------------------------------

    def _build_card(self, icon_name: str, title: str) -> QFrame:
        """Создаёт карточку с заголовком: SVG-иконка слева + текст справа."""
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(15, 12, 15, 12)
        card_layout.setSpacing(8)

        # Шапка карточки — иконка + текст
        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        header_row.setAlignment(Qt.AlignVCenter)

        header_icon = QLabel()
        header_icon.setPixmap(
            load_icon(icon_name, size=18,
                      color=ICON_COLOR_HOVER).pixmap(18, 18)
        )
        header_icon.setFixedSize(18, 18)
        header_row.addWidget(header_icon)

        header_text = QLabel(title)
        header_text.setStyleSheet("font-size: 11pt; font-weight: 600;")
        header_row.addWidget(header_text)
        header_row.addStretch()

        card_layout.addLayout(header_row)
        return card

    def _set_status(self, icon_name: str | None, text: str,
                     color: str = "#9ca3bf"):
        """Устанавливает статус-строку с иконкой и текстом.

        Args:
            icon_name: имя SVG-файла или None (скрыть иконку).
            text: текст статуса.
            color: цвет иконки и текста (hex).
        """
        if icon_name:
            self.update_status_icon.setPixmap(
                load_icon(icon_name, size=16, color=color).pixmap(16, 16)
            )
            self.update_status_icon.show()
        else:
            self.update_status_icon.hide()

        self.update_status_label.setText(text)
        self.update_status_label.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------
    # Загрузка/сохранение конфига
    # ------------------------------------------------------------------

    def _load_from_config(self):
        self.key_path_edit.setText(self.config.get('ssh_key_path', ''))
        self.default_password_edit.setText(
            self.config.get('default_device_password', '')
        )
        self.auto_check_checkbox.setChecked(
            self.config.get('auto_check_updates', True)
        )

    def _save_config(self):
        self.config.set('ssh_key_path', self.key_path_edit.text())
        self.config.set(
            'default_device_password', self.default_password_edit.text()
        )
        self.config.save()
        self.log_info.emit("Настройки сохранены")

    def _on_browse_key(self):
        current = self.key_path_edit.text()
        start_dir = str(Path(current).parent) if current else ""
        fp, _ = QFileDialog.getOpenFileName(
            self, "Выберите SSH-ключ", start_dir, "Все файлы (*)"
        )
        if fp:
            self.key_path_edit.setText(fp)
            self.config.set_and_save('ssh_key_path', fp)
            self.log_info.emit(f"Выбран SSH-ключ: {fp}")

    # ------------------------------------------------------------------
    # Проверка обновлений
    # ------------------------------------------------------------------

    def _on_check_updates(self):
        self.check_updates_btn.setEnabled(False)
        self._set_status(None, "Проверка обновлений...")
        self.log_info.emit("Проверка обновлений на GitHub...")

        self._update_thread = UpdateCheckThread()
        self._update_thread.result.connect(self._on_update_check_result)
        self._update_thread.start()

    def _on_update_check_result(self, release):
        self.check_updates_btn.setEnabled(True)
        if release is None:
            self._set_status(
                "warning.svg",
                "Не удалось проверить обновления (нет интернета?)",
                color=ICON_COLOR_WARNING,
            )
            self.log_warning.emit("Не удалось проверить обновления")
            return

        if is_update_available(release):
            body = release.body
            if len(body) > 300:
                body = body[:300] + "..."
            self._set_status(
                "download.svg",
                f"Доступна версия {release.version}\n\n{body}",
                color=ICON_COLOR_SUCCESS,
            )
            self.log_info.emit(f"Доступна версия {release.version}")
            reply = QMessageBox.question(
                self, "Обновление",
                f"Доступна новая версия {release.version}.\n"
                f"Текущая: {VERSION}\n\n"
                f"Скачать обновление?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self._start_download(release)
        else:
            self._set_status(
                "success.svg",
                f"У вас установлена последняя версия ({VERSION})",
                color=ICON_COLOR_SUCCESS,
            )
            self.log_info.emit(f"Актуальная версия v{VERSION}")