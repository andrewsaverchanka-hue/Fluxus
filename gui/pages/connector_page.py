"""Страница коннектора: подключение к устройствам и скачивание/стриминг логов."""

import re
from datetime import datetime, time
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot, QDate, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QSpinBox, QRadioButton, QButtonGroup, QDateEdit,
    QMessageBox, QLineEdit, QScrollArea,
)

from config.config_manager import get_config
from connector.device_worker import DeviceWorker
from connector.device_info import SystemInfo
from gui.widgets.device_card import DeviceCard
from gui.icon_loader import (
    load_icon,
    ICON_COLOR_NORMAL,
    ICON_COLOR_HOVER,
)


VPN_PATTERN = re.compile(r"^10\.8\.\d{1,3}\.\d{1,3}$")


class ClickableDateEdit(QDateEdit):
    """QDateEdit, который открывает календарь при клике по любому месту поля."""

    def __init__(self, date, parent=None):
        super().__init__(date, parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd.MM.yyyy")
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.calendarWidget().setFocus()
            self.setFocus()
            if hasattr(self, '_show_calendar'):
                self._show_calendar()
            else:
                super().mousePressEvent(event)
                return
        super().mousePressEvent(event)


class ConnectorPage(QWidget):
    log_info = Signal(str)
    log_success = Signal(str)
    log_warning = Signal(str)
    log_error = Signal(str)
    logs_ready_for_analysis = Signal(list)
    streams_ready_for_analysis = Signal(str, list, str)

    GRID_COLUMNS = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_config()
        self.workers: dict[str, DeviceWorker] = {}
        self.cards: dict[str, DeviceCard] = {}
        self._next_device_id = 1
        self._last_request = None
        self._build_ui()

    # ------------------------------------------------------------------
    # Утилита для создания заголовков с SVG-иконкой
    # ------------------------------------------------------------------

    def _make_header_row(self, icon_name: str, text: str,
                         icon_size: int = 28,
                         font_style: str = "pageTitle") -> QHBoxLayout:
        """Создаёт горизонтальный layout: SVG-иконка слева + текст справа.

        Args:
            icon_name:  имя SVG-файла в assets/icons/
            text:       текст заголовка
            icon_size:  размер иконки в px
            font_style: имя стиля для QLabel (objectName)
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.setAlignment(Qt.AlignVCenter)

        icon_label = QLabel()
        icon_label.setFixedSize(icon_size, icon_size)
        icon_label.setPixmap(
            load_icon(icon_name, size=icon_size,
                      color=ICON_COLOR_HOVER).pixmap(icon_size, icon_size)
        )
        row.addWidget(icon_label, 0, Qt.AlignVCenter)

        text_label = QLabel(text)
        text_label.setObjectName(font_style)
        text_label.setStyleSheet("QLabel { padding: 0px; }")
        row.addWidget(text_label, 0, Qt.AlignVCenter)
        row.addStretch()

        return row

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(10)

        # ===== Заголовок страницы =====
        layout.addLayout(
            self._make_header_row("ssh.svg", "Подключение SSH")
        )

        # ===== Карточка: добавление устройства =====
        add_card = QFrame()
        add_card.setObjectName("card")
        add_layout = QHBoxLayout(add_card)
        add_layout.setContentsMargins(12, 10, 12, 10)
        add_layout.setSpacing(8)

        plus_icon = QLabel()
        plus_icon.setFixedSize(18, 18)
        plus_icon.setPixmap(
            load_icon("plus.svg", size=18,
                      color=ICON_COLOR_NORMAL).pixmap(18, 18)
        )
        add_layout.addWidget(plus_icon)

        self.vpn_input = QLineEdit()
        self.vpn_input.setPlaceholderText("10.8.0.1")
        self.vpn_input.setMinimumWidth(140)
        self.vpn_input.setMaximumWidth(180)
        self.vpn_input.returnPressed.connect(self._on_add_device)
        add_layout.addWidget(self.vpn_input)

        self.comment_input = QLineEdit()
        self.comment_input.setPlaceholderText("Комментарий (опционально)")
        self.comment_input.returnPressed.connect(self._on_add_device)
        add_layout.addWidget(self.comment_input, stretch=1)

        add_btn = QPushButton("  Добавить")
        add_btn.setObjectName("compactPrimaryButton")
        add_btn.setIcon(load_icon("plus.svg", size=14))
        add_btn.setIconSize(QSize(14, 14))
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._on_add_device)
        add_layout.addWidget(add_btn)

        layout.addWidget(add_card)

        # ===== Карточка: период =====
        period_card = QFrame()
        period_card.setObjectName("card")
        period_layout = QHBoxLayout(period_card)
        period_layout.setContentsMargins(12, 10, 12, 10)
        period_layout.setSpacing(10)

        # Иконка календаря + надпись
        cal_icon = QLabel()
        cal_icon.setFixedSize(16, 16)
        cal_icon.setPixmap(
            load_icon("calendar.svg", size=16,
                      color=ICON_COLOR_NORMAL).pixmap(16, 16)
        )
        period_layout.addWidget(cal_icon)
        period_layout.addWidget(QLabel("Период:"))

        self.mode_group = QButtonGroup(self)
        self.mode_hours_rb = QRadioButton("За последние")
        self.mode_hours_rb.setChecked(True)
        self.mode_group.addButton(self.mode_hours_rb, 0)
        period_layout.addWidget(self.mode_hours_rb)
        self.hours_spin = QSpinBox()
        self.hours_spin.setRange(1, 1440)
        self.hours_spin.setValue(24)
        self.hours_spin.setSuffix(" ч")
        self.hours_spin.setMaximumWidth(100)
        self.hours_spin.setButtonSymbols(QSpinBox.NoButtons)
        period_layout.addWidget(self.hours_spin)

        period_layout.addSpacing(20)

        self.mode_range_rb = QRadioButton("Диапазон:")
        self.mode_group.addButton(self.mode_range_rb, 1)
        period_layout.addWidget(self.mode_range_rb)

        self.from_dt_edit = ClickableDateEdit(QDate.currentDate().addDays(-1))
        self.from_dt_edit.setMinimumWidth(110)
        self.from_dt_edit.setMaximumWidth(120)
        period_layout.addWidget(self.from_dt_edit)
        period_layout.addWidget(QLabel("—"))
        self.to_dt_edit = ClickableDateEdit(QDate.currentDate())
        self.to_dt_edit.setMinimumWidth(110)
        self.to_dt_edit.setMaximumWidth(120)
        period_layout.addWidget(self.to_dt_edit)

        period_layout.addStretch()
        layout.addWidget(period_card)

        # ===== Карточка: устройства =====
        devices_card = QFrame()
        devices_card.setObjectName("card")
        devices_card_layout = QVBoxLayout(devices_card)
        devices_card_layout.setContentsMargins(12, 10, 12, 10)
        devices_card_layout.setSpacing(8)

        # Шапка карточки: иконка device + "Устройства" + кнопка "Очистить"
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)
        hdr_row.setAlignment(Qt.AlignVCenter)

        device_icon = QLabel()
        device_icon.setFixedSize(16, 16)
        device_icon.setPixmap(
            load_icon("device.svg", size=16,
                      color=ICON_COLOR_HOVER).pixmap(16, 16)
        )
        hdr_row.addWidget(device_icon)

        hdr = QLabel("Устройства")
        hdr.setStyleSheet("font-size: 10pt; font-weight: 600;")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()

        clear_btn = QPushButton("  Очистить все")
        clear_btn.setObjectName("compactButton")
        clear_btn.setIcon(load_icon("broom.svg", size=14))
        clear_btn.setIconSize(QSize(14, 14))
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._on_clear_devices)
        hdr_row.addWidget(clear_btn)
        devices_card_layout.addLayout(hdr_row)

        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QFrame.NoFrame)
        self.cards_scroll.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollArea > QWidget > QWidget { background: transparent; }
        """)

        self.cards_container = QWidget()
        self.cards_grid = QGridLayout(self.cards_container)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setSpacing(10)
        self.cards_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.cards_scroll.setWidget(self.cards_container)
        devices_card_layout.addWidget(self.cards_scroll, stretch=1)

        # ===== Нижний ряд: действия с устройствами =====
        actions_row = QHBoxLayout()
        actions_row.setSpacing(6)

        self.connect_btn = QPushButton("  Подключиться")
        self.connect_btn.setObjectName("compactPrimaryButton")
        self.connect_btn.setIcon(load_icon("connect.svg", size=14))
        self.connect_btn.setIconSize(QSize(14, 14))
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.clicked.connect(self._on_connect_selected)
        actions_row.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("  Отключить всё")
        self.disconnect_btn.setObjectName("compactButton")
        self.disconnect_btn.setIcon(load_icon("disconnect.svg", size=14))
        self.disconnect_btn.setIconSize(QSize(14, 14))
        self.disconnect_btn.setCursor(Qt.PointingHandCursor)
        self.disconnect_btn.clicked.connect(self._on_disconnect_all)
        actions_row.addWidget(self.disconnect_btn)

        self.remove_btn = QPushButton("  Удалить")
        self.remove_btn.setObjectName("compactButton")
        self.remove_btn.setIcon(load_icon("trash.svg", size=14))
        self.remove_btn.setIconSize(QSize(14, 14))
        self.remove_btn.setCursor(Qt.PointingHandCursor)
        self.remove_btn.clicked.connect(self._on_remove_selected)
        actions_row.addWidget(self.remove_btn)

        actions_row.addSpacing(16)

        self.fetch_btn = QPushButton("  Скачать логи")
        self.fetch_btn.setObjectName("compactButton")
        self.fetch_btn.setIcon(load_icon("download.svg", size=14))
        self.fetch_btn.setIconSize(QSize(14, 14))
        self.fetch_btn.setCursor(Qt.PointingHandCursor)
        self.fetch_btn.clicked.connect(self._on_fetch_logs)
        actions_row.addWidget(self.fetch_btn)

        self.stream_btn = QPushButton("  Открыть на устройстве")
        self.stream_btn.setObjectName("compactPrimaryButton")
        self.stream_btn.setIcon(load_icon("stream.svg", size=14))
        self.stream_btn.setIconSize(QSize(14, 14))
        self.stream_btn.setCursor(Qt.PointingHandCursor)
        self.stream_btn.setToolTip(
            "Читает логи напрямую с устройства через grep/zgrep,\n"
            "без записи на диск."
        )
        self.stream_btn.clicked.connect(self._on_stream_logs)
        actions_row.addWidget(self.stream_btn)

        actions_row.addStretch()
        devices_card_layout.addLayout(actions_row)

        layout.addWidget(devices_card, stretch=1)

    # ========================================================================
    # Добавление и удаление
    # ========================================================================

    def _on_add_device(self):
        vpn = self.vpn_input.text().strip()
        comment = self.comment_input.text().strip()

        if not vpn:
            QMessageBox.warning(self, "Нет VPN", "Введите VPN-адрес.")
            return
        if not VPN_PATTERN.match(vpn):
            QMessageBox.warning(
                self, "Неверный формат",
                "VPN-адрес должен быть в формате 10.8.x.x"
            )
            return

        for card in self.cards.values():
            if card.vpn_host == vpn:
                QMessageBox.information(
                    self, "Уже добавлено",
                    f"Устройство с VPN {vpn} уже есть."
                )
                return

        device_id = f"{self._next_device_id:03d}"
        self._next_device_id += 1

        card = DeviceCard(device_id, vpn, comment, parent=self.cards_container)
        self.cards[device_id] = card

        n = len(self.cards) - 1
        row = n // self.GRID_COLUMNS
        col = n % self.GRID_COLUMNS
        self.cards_grid.addWidget(card, row, col)

        self.log_info.emit(f"Добавлено устройство {vpn}")

        self.vpn_input.clear()
        self.comment_input.clear()
        self.vpn_input.setFocus()

    def _rebuild_grid(self):
        for card in self.cards.values():
            self.cards_grid.removeWidget(card)
        for i, (device_id, card) in enumerate(self.cards.items()):
            row = i // self.GRID_COLUMNS
            col = i % self.GRID_COLUMNS
            self.cards_grid.addWidget(card, row, col)

    def _on_clear_devices(self):
        if not self.cards:
            return
        if self.workers:
            reply = QMessageBox.question(
                self, "Активные подключения",
                "Есть активные подключения. Отключить и очистить?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
            self._on_disconnect_all()
        reply = QMessageBox.question(
            self, "Очистить", "Удалить все устройства?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            for card in list(self.cards.values()):
                self.cards_grid.removeWidget(card)
                card.deleteLater()
            self.cards.clear()
            self._next_device_id = 1
            self.log_info.emit("Устройства очищены")

    def _on_remove_selected(self):
        selected_ids = [d_id for d_id, c in self.cards.items() if c.is_checked()]
        if not selected_ids:
            QMessageBox.warning(
                self, "Нет выбора",
                "Выберите устройства для удаления (клик по карточке)."
            )
            return
        for device_id in selected_ids:
            worker = self.workers.get(device_id)
            if worker and worker.isRunning():
                worker.request_disconnect()
            card = self.cards.pop(device_id)
            self.cards_grid.removeWidget(card)
            card.deleteLater()
        self._rebuild_grid()
        self.log_info.emit(f"Удалено устройств: {len(selected_ids)}")

    def _get_selected_devices(self) -> list[dict]:
        result = []
        for device_id, card in self.cards.items():
            if card.is_checked():
                result.append({
                    'id': device_id,
                    'host': card.vpn_host,
                    'comment': card.comment,
                })
        return result

    # ========================================================================
    # Подключение / отключение
    # ========================================================================

    def _on_connect_selected(self):
        selected = self._get_selected_devices()
        if not selected:
            QMessageBox.warning(
                self, "Нет выбора",
                "Выберите хотя бы одно устройство (клик по карточке)."
            )
            return
        key_path = self.config.get('ssh_key_path', '')
        if not key_path or not Path(key_path).exists():
            QMessageBox.warning(
                self, "SSH-ключ не настроен",
                "Укажите SSH-ключ в разделе «Настройки»."
            )
            return
        default_pwd = self.config.get('default_device_password', '')

        for dev in selected:
            device_id = dev['id']
            if device_id in self.workers and self.workers[device_id].isRunning():
                self.log_warning.emit(
                    f"Устройство {device_id} уже подключается/подключено"
                )
                continue
            device = {
                'id': device_id, 'host': dev['host'],
                'port': 22, 'username': 'root', 'password': '',
            }
            worker = DeviceWorker(device, key_path, default_pwd, parent=self)
            worker.status_changed.connect(self._on_worker_status)
            worker.log_message.connect(self._on_worker_log)
            worker.logs_downloaded.connect(self._on_logs_downloaded)
            worker.streams_ready.connect(self._on_streams_ready)
            worker.download_progress.connect(self._on_download_progress)
            worker.error.connect(self._on_worker_error)
            worker.stream_error.connect(self._on_worker_stream_error)
            worker.system_info_ready.connect(self._on_system_info_ready)
            self.workers[device_id] = worker
            worker.start()
            self.log_info.emit(f"Подключение к {dev['host']}...")

    def _on_disconnect_all(self):
        for device_id, worker in list(self.workers.items()):
            if worker.isRunning():
                worker.request_disconnect()
                self.log_info.emit(f"Отключение {device_id}...")

    # ========================================================================
    # Fetch / Stream
    # ========================================================================

    def _build_period_params(self) -> dict | None:
        if self.mode_hours_rb.isChecked():
            return {'mode': 'hours', 'params': {'hours': self.hours_spin.value()}}
        from_date = self.from_dt_edit.date().toPython()
        to_date = self.to_dt_edit.date().toPython()
        start = datetime.combine(from_date, time.min)
        end = datetime.combine(to_date, time.max)
        if start >= end:
            QMessageBox.warning(
                self, "Неверный диапазон",
                "Дата начала должна быть раньше даты конца."
            )
            return None
        return {'mode': 'date_range', 'params': {'start': start, 'end': end}}

    def _on_fetch_logs(self):
        selected = self._get_selected_devices()
        if not selected:
            QMessageBox.warning(self, "Нет выбора",
                                "Выберите устройства для скачивания.")
            return
        period = self._build_period_params()
        if period is None:
            return
        params = dict(period['params'])
        params['stream_mode'] = False
        self._dispatch_fetch_requests(selected, period['mode'], params,
                                       mode_label='Скачивание')

    def _on_stream_logs(self):
        selected = self._get_selected_devices()
        if not selected:
            QMessageBox.warning(self, "Нет выбора",
                                "Выберите устройства для стриминга.")
            return
        period = self._build_period_params()
        if period is None:
            return
        params = dict(period['params'])
        params['stream_mode'] = True
        self._dispatch_fetch_requests(selected, period['mode'], params,
                                       mode_label='Стриминг')

    def _dispatch_fetch_requests(self, selected: list, mode: str,
                                 params: dict, mode_label: str):
        sent = 0
        skipped = []
        device_ids = []
        for dev in selected:
            device_id = dev['id']
            worker = self.workers.get(device_id)
            if worker is None or not worker.isRunning():
                skipped.append(device_id)
                continue
            worker.request_fetch_logs(mode, params)
            self.log_info.emit(f"{mode_label} запрос для {device_id}")
            device_ids.append(device_id)
            sent += 1

        self._last_request = {
            'mode': mode, 'params': params, 'device_ids': device_ids,
        }
        if skipped:
            self.log_warning.emit(f"Не подключены: {', '.join(skipped)}")
        if sent == 0:
            QMessageBox.warning(
                self, "Нет подключённых",
                "Сначала подключитесь к выбранным устройствам."
            )

    # ========================================================================
    # Обработчики сигналов воркеров
    # ========================================================================

    @Slot(str, str, str)
    def _on_worker_status(self, device_id, status, label):
        card = self.cards.get(device_id)
        if card:
            card.set_status(status, label)
        if status == 'connected':
            self.log_success.emit(f"{device_id}: {label}")
        elif status == 'disconnected':
            self.workers.pop(device_id, None)

    @Slot(str, object)
    def _on_system_info_ready(self, device_id, sys_info: SystemInfo):
        card = self.cards.get(device_id)
        if card:
            card.set_system_info(sys_info)

    @Slot(str, str, str)
    def _on_worker_log(self, device_id, level, msg):
        prefix = f"[{device_id}]"
        if level == 'error':
            self.log_error.emit(f"{prefix} {msg}")
        elif level == 'warning':
            self.log_warning.emit(f"{prefix} {msg}")
        else:
            self.log_info.emit(f"{prefix} {msg}")

    @Slot(str, int, int, str)
    def _on_download_progress(self, device_id, current, total, filename):
        card = self.cards.get(device_id)
        if card:
            card.set_progress(current, total, filename)

    @Slot(str, str, list)
    def _on_logs_downloaded(self, device_id, folder_path, file_paths):
        card = self.cards.get(device_id)
        if card:
            card.reset_progress()
        self.log_success.emit(
            f"{device_id}: скачано {len(file_paths)} файлов"
        )
        reply = QMessageBox.question(
            self, "Логи скачаны",
            f"Устройство {device_id}: скачано {len(file_paths)} файлов.\n\n"
            f"Открыть в анализаторе?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.logs_ready_for_analysis.emit(file_paths)

    @Slot(str, list, str)
    def _on_streams_ready(self, device_id, streams_data, device_label):
        card = self.cards.get(device_id)
        if card:
            card.reset_progress()
        total_chars = sum(len(text) for _, text, _ in streams_data)
        self.log_success.emit(
            f"{device_id}: собрано {len(streams_data)} стримов, "
            f"{total_chars // 1024} КБ"
        )
        self.streams_ready_for_analysis.emit(
            device_id, streams_data, device_label
        )

    @Slot(str, str)
    def _on_worker_error(self, device_id, msg):
        card = self.cards.get(device_id)
        if card:
            card.reset_progress()
        self.log_error.emit(f"[{device_id}] {msg}")

    @Slot(str, str, str)
    def _on_worker_stream_error(self, device_id, category, msg):
        card = self.cards.get(device_id)
        if card:
            card.reset_progress()

        if category == 'tool_missing':
            title = "Нет инструментов на устройстве"
            question = (
                f"На {device_id} недоступны zgrep/zcat.\n\n"
                f"Скачать логи на диск?"
            )
        elif category == 'channel':
            title = "SSH-канал оборван"
            question = (
                f"Устройство {device_id}: ошибка SSH.\n\n{msg[:200]}\n\n"
                f"Скачать логи на диск?"
            )
        elif category == 'empty':
            title = "grep не нашёл данных"
            question = (
                f"На {device_id} grep не нашёл нужных строк.\n\n"
                f"Скачать логи на диск?"
            )
        else:
            title = "Ошибка стриминга"
            question = (
                f"Устройство {device_id}: {msg[:200]}\n\n"
                f"Скачать логи на диск?"
            )

        self.log_error.emit(f"[{device_id}] Стрим провалился: {msg}")
        reply = QMessageBox.question(
            self, title, question,
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        if not self._last_request:
            self.log_warning.emit("Нет параметров для fallback")
            return
        worker = self.workers.get(device_id)
        if worker is None or not worker.isRunning():
            self.log_warning.emit(f"{device_id} не подключено")
            return
        fallback_params = dict(self._last_request['params'])
        fallback_params['stream_mode'] = False
        worker.request_fetch_logs(self._last_request['mode'], fallback_params)
        self.log_info.emit(f"[{device_id}] Fallback: пробуем скачать")