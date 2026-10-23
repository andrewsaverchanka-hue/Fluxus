"""Страница настроек приложения."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QFileDialog, QFrame, QCheckBox, QMessageBox,
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


class UpdateCheckThread(QThread):
    """Проверка обновлений в фоне."""
    result = Signal(object)

    def run(self):
        release = check_latest_release()
        self.result.emit(release)


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
                self.log_info.emit(
                    "Скачивание обновления будет реализовано в следующем релизе"
                )
                # TODO: добавить реальное скачивание и установку
        else:
            self._set_status(
                "success.svg",
                f"У вас установлена последняя версия ({VERSION})",
                color=ICON_COLOR_SUCCESS,
            )
            self.log_info.emit(f"Актуальная версия v{VERSION}")