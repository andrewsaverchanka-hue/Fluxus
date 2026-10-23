"""Компактная карточка устройства для страницы коннектора.

Вся карточка — это кликабельная кнопка-переключатель.
Статусные индикаторы рисуются через Unicode-символы (●/○) с CSS-окраской,
а не эмодзи — так круги не обрезаются на Windows.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar,
    QSizePolicy,
)

from connector.device_info import SystemInfo


# Статусы: (символ_круга, текст, цвет)
# ● = U+25CF BLACK CIRCLE — обычный Unicode, рендерится без проблем
# ○ = U+25CB WHITE CIRCLE — пустой круг для "не подключено"
STATUS_STYLES = {
    'disconnected': ('○', 'Не подключено', '#9ca3bf'),
    'connecting':   ('●', 'Подключение...', '#f59e0b'),
    'connected':    ('●', 'Подключено',    '#10b981'),
    'error':        ('●', 'Ошибка',         '#ef4444'),
}


class DeviceCard(QFrame):
    """Карточка одного устройства. Клик по любому месту — toggle выбор."""

    checked_changed = Signal(str, bool)

    def __init__(self, device_id: str, vpn_host: str, comment: str = "",
                 parent=None):
        super().__init__(parent)
        self.device_id = device_id
        self.vpn_host = vpn_host
        self.comment = comment
        self._status = 'disconnected'
        self._device_type = None
        self._device_label = None
        self._sys_info: SystemInfo | None = None
        self._is_checked = False

        self.setObjectName("deviceCard")
        self.setFixedWidth(250)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"ID: #{device_id}\nVPN: {vpn_host}")

        self._build_ui()
        self._apply_style()
        self._refresh_display()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        # ===== Шапка: VPN + индикатор выбора + тип =====
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)

        self.vpn_label = QLabel(self.vpn_host)
        self.vpn_label.setStyleSheet("""
            color: #e4e5f1;
            font-weight: 600;
            font-size: 12pt;
        """)
        header_row.addWidget(self.vpn_label)

        header_row.addStretch()

        self.check_indicator = QLabel("")
        self.check_indicator.setFixedSize(24, 24)
        self.check_indicator.setAlignment(Qt.AlignCenter)
        self._update_check_indicator_style()
        header_row.addWidget(self.check_indicator)

        self.type_badge = QLabel("")
        self.type_badge.setMinimumHeight(22)
        self.type_badge.setStyleSheet("""
            color: #10b981;
            font-weight: 600;
            font-size: 10pt;
            padding: 2px 8px;
            background-color: rgba(16, 185, 129, 0.15);
            border-radius: 4px;
        """)
        self.type_badge.setVisible(False)
        header_row.addWidget(self.type_badge)

        root.addLayout(header_row)

        # ===== Статус как два отдельных QLabel в горизонтальном layout =====
        # Левая часть — цветной круг (Unicode-символ, не эмодзи)
        # Правая часть — текст статуса
        # Такой подход даёт полный контроль над размером и позицией круга,
        # и исключает проблему обрезания эмодзи на Windows.
        status_row_widget = QFrame()
        status_row_widget.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
        )
        status_row = QHBoxLayout(status_row_widget)
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)

        self.status_dot = QLabel("")
        self.status_dot.setFixedWidth(16)
        self.status_dot.setAlignment(Qt.AlignCenter)
        status_row.addWidget(self.status_dot)

        self.status_text = QLabel("")
        self.status_text.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        status_row.addWidget(self.status_text, stretch=1)

        status_row.addStretch()
        root.addWidget(status_row_widget)

        # ===== Разделитель =====
        self.separator = QFrame()
        self.separator.setFrameShape(QFrame.HLine)
        self.separator.setStyleSheet("color: #34354e; max-height: 1px;")
        self.separator.setVisible(False)
        root.addWidget(self.separator)

        # ===== Информация о системе =====
        self.info_container = QVBoxLayout()
        self.info_container.setContentsMargins(0, 0, 0, 0)
        self.info_container.setSpacing(3)

        self.device_id_row = QLabel("")
        self.device_id_row.setMinimumHeight(20)
        self.device_id_row.setStyleSheet(
            "color: #c9cad8; font-size: 9pt; padding: 1px 0px;"
        )
        self.info_container.addWidget(self.device_id_row)

        self.ram_row = QLabel("")
        self.ram_row.setMinimumHeight(20)
        self.ram_row.setStyleSheet(
            "color: #c9cad8; font-size: 9pt; padding: 1px 0px;"
        )
        self.info_container.addWidget(self.ram_row)

        self.cpu_row = QLabel("")
        self.cpu_row.setMinimumHeight(20)
        self.cpu_row.setStyleSheet(
            "color: #c9cad8; font-size: 9pt; padding: 1px 0px;"
        )
        self.info_container.addWidget(self.cpu_row)

        self.uptime_row = QLabel("")
        self.uptime_row.setMinimumHeight(20)
        self.uptime_row.setStyleSheet(
            "color: #c9cad8; font-size: 9pt; padding: 1px 0px;"
        )
        self.info_container.addWidget(self.uptime_row)

        info_widget = QFrame()
        info_widget.setLayout(self.info_container)
        info_widget.setStyleSheet(
            "QFrame { background-color: transparent; border: none; }"
        )
        info_widget.setVisible(False)
        self.info_widget = info_widget
        root.addWidget(info_widget)

        # ===== Комментарий =====
        if self.comment:
            self.comment_label = QLabel(f"💬 {self.comment}")
            self.comment_label.setStyleSheet(
                "color: #9ca3bf; font-size: 8pt; font-style: italic;"
            )
            self.comment_label.setWordWrap(True)
            root.addWidget(self.comment_label)
        else:
            self.comment_label = None

        # ===== Прогресс-бар =====
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.progress_bar.setMaximumHeight(8)
        self.progress_bar.setVisible(False)
        root.addWidget(self.progress_bar)

    def _update_check_indicator_style(self):
        if self._is_checked:
            self.check_indicator.setText("✓")
            self.check_indicator.setStyleSheet("""
                QLabel {
                    background-color: #8b5cf6;
                    color: white;
                    font-weight: 700;
                    font-size: 13pt;
                    border-radius: 12px;
                }
            """)
        else:
            self.check_indicator.setText("")
            self.check_indicator.setStyleSheet("""
                QLabel {
                    background-color: transparent;
                    border: 1.5px solid #4a4b63;
                    border-radius: 12px;
                }
            """)

    def _apply_style(self):
        if self._is_checked:
            self.setStyleSheet("""
                QFrame#deviceCard {
                    background-color: rgba(139, 92, 246, 0.18);
                    border: 2px solid #8b5cf6;
                    border-radius: 10px;
                }
                QFrame#deviceCard:hover {
                    background-color: rgba(139, 92, 246, 0.28);
                    border: 2px solid #a78bfa;
                }
            """)
        else:
            self.setStyleSheet("""
                QFrame#deviceCard {
                    background-color: #232438;
                    border: 2px solid #34354e;
                    border-radius: 10px;
                }
                QFrame#deviceCard:hover {
                    background-color: #2a2b40;
                    border: 2px solid #6366f1;
                }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.set_checked(not self._is_checked)
        super().mousePressEvent(event)

    def is_checked(self) -> bool:
        return self._is_checked

    def set_checked(self, value: bool):
        if self._is_checked == value:
            return
        self._is_checked = value
        self._update_check_indicator_style()
        self._apply_style()
        self.checked_changed.emit(self.device_id, value)

    def set_status(self, status: str, device_label: str = ""):
        self._status = status
        if status == 'connected' and device_label:
            if '_' in device_label:
                parts = device_label.split('_', 1)
                self._device_type = parts[0]
                self._device_label = parts[1] if len(parts) > 1 else ''
        if status in ('disconnected', 'error'):
            self._device_type = None
            self._device_label = None
            self._sys_info = None
        self._refresh_display()

    def set_system_info(self, sys_info: SystemInfo):
        self._sys_info = sys_info
        self._refresh_display()

    def set_progress(self, current: int, total: int, filename: str = ""):
        if total <= 0:
            self.progress_bar.setVisible(False)
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        if filename:
            self.progress_bar.setToolTip(f"{current}/{total}: {filename}")

    def reset_progress(self):
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)

    def _refresh_display(self):
        dot_symbol, text, color = STATUS_STYLES.get(
            self._status, STATUS_STYLES['disconnected']
        )
        # Круг — отдельный QLabel с цветом через CSS, рендерится обычным шрифтом
        self.status_dot.setText(dot_symbol)
        self.status_dot.setStyleSheet(f"""
            color: {color};
            font-size: 14pt;
            font-weight: 400;
            padding: 0px;
            background: transparent;
        """)
        # Текст статуса — отдельный QLabel с тем же цветом
        self.status_text.setText(text)
        self.status_text.setStyleSheet(f"""
            color: {color};
            font-size: 10pt;
            padding: 0px;
            background: transparent;
        """)

        if self._status == 'connected' and self._device_type:
            type_emoji = "🖥️" if self._device_type == 'Lima' else "💳"
            self.type_badge.setText(f"{type_emoji} {self._device_type}")
            self.type_badge.setVisible(True)

            self.separator.setVisible(True)
            self.info_widget.setVisible(True)

            if self._device_type == 'Lima':
                id_icon = "🔑 ID"
            elif self._device_type == 'W2':
                id_icon = "💳 Счёт"
            else:
                id_icon = "🏷️ ID"
            label_short = (self._device_label[:16] + '…'
                           if self._device_label and len(self._device_label) > 16
                           else (self._device_label or '?'))
            self.device_id_row.setText(f"{id_icon}: {label_short}")

            if self._sys_info:
                self.ram_row.setText(f"💾 RAM: {self._sys_info.mem_display()}")
                cpu_temp = self._sys_info.cpu_temp_display()
                cpu_load = self._sys_info.cpu_load_display()
                self.cpu_row.setText(f"🌡️ CPU: {cpu_temp} / {cpu_load}")
                self.uptime_row.setText(
                    f"⏱ uptime: {self._sys_info.uptime_display()}"
                )
            else:
                self.ram_row.setText("💾 RAM: загрузка...")
                self.cpu_row.setText("🌡️ CPU: загрузка...")
                self.uptime_row.setText("⏱ uptime: загрузка...")
        else:
            self.type_badge.setVisible(False)
            self.separator.setVisible(False)
            self.info_widget.setVisible(False)