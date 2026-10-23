"""Страница анализатора логов — главная страница приложения."""

import os
import re
import io
import traceback
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from PySide6.QtCore import Qt, QThread, QSize, Signal, Slot, QByteArray
from PySide6.QtGui import QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QListWidget, QListWidgetItem, QFrame,
    QScrollArea, QSizePolicy,
)

from config.config_manager import get_config
from core.utils import detect_log_type
from parsers.lima_parser import LogAnalyzer
from parsers.w2_frontend_parser import W2FrontendAnalyzer
from parsers.w2_lowlevel_parser import W2LowLevelAnalyzer
from parsers.serializer import AnalysisSerializer
from gui.windows.analysis_result_window import AnalysisResultWindow
from gui.icon_loader import (
    load_icon,
    ICON_COLOR_NORMAL,
    ICON_COLOR_HOVER,
    ICON_COLOR_MUTED,
)


# =============================================================================
# ИКОНКА КРЕСТИКА — встроенный SVG, не требует внешних файлов
# =============================================================================

CLOSE_ICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2.5" stroke-linecap="round"
     stroke-linejoin="round">
    <line x1="6" y1="6" x2="18" y2="18"/>
    <line x1="18" y1="6" x2="6" y2="18"/>
</svg>
"""


def _make_svg_icon(svg_text: str, size: int = 16,
                   color: str = "#9ca3bf") -> QIcon:
    """Создаёт QIcon из SVG-строки с указанным цветом."""
    svg_colored = svg_text.replace("currentColor", color)
    renderer = QSvgRenderer(QByteArray(svg_colored.encode('utf-8')))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


# =============================================================================
# СТИЛЬ ДЛЯ PRIMARY-КНОПОК
# =============================================================================

PRIMARY_BUTTON_STYLE = """
    QPushButton {
        background-color: #6366f1;
        color: #ffffff;
        border: none;
        border-radius: 10px;
        padding: 8px 20px;
        font-size: 10pt;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #7c7fe8;
    }
    QPushButton:pressed {
        background-color: #4f52d4;
    }
    QPushButton:disabled {
        background-color: #2a2b40;
        color: #5b5f7a;
    }
"""


# =============================================================================
# ВОРКЕР АНАЛИЗА
# =============================================================================

class AnalysisWorker(QThread):
    progress = Signal(int, str)
    device_done = Signal(str, dict)
    finished_all = Signal(dict)
    error = Signal(str)

    def __init__(self, files_by_device: dict, parent=None):
        super().__init__(parent)
        self.files_by_device = files_by_device
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            devices_data = {}
            for device_id, files in self.files_by_device.items():
                if self._stop:
                    return
                analyzer = LogAnalyzer()
                w2_front = W2FrontendAnalyzer()
                w2_ll = W2LowLevelAnalyzer()
                for fp in files:
                    if self._stop:
                        return
                    log_type = detect_log_type(fp)
                    if log_type == 'frontend':
                        w2_front.parse_log_file(fp, self._progress_cb)
                    elif log_type == 'devices':
                        analyzer.parse_log_file(fp, self._progress_cb)
                        w2_ll.parse_log_file(fp, self._progress_cb)
                    else:
                        analyzer.parse_log_file(fp, self._progress_cb)
                        w2_front.parse_log_file(fp, self._progress_cb)
                        w2_ll.parse_log_file(fp, self._progress_cb)
                analyzer.build_sessions()
                w2_front.build_sessions()
                w2_ll.build_sessions()
                w2_ll.correlate_with_frontend(w2_front.sessions)
                device_data = {
                    'device_id': device_id,
                    'label': self._make_label(device_id, files),
                    'files': files,
                    'lima': analyzer,
                    'w2_frontend': w2_front,
                    'w2_lowlevel': w2_ll,
                }
                devices_data[device_id] = device_data
                self.device_done.emit(device_id, device_data)
            self.finished_all.emit(devices_data)
        except Exception as e:
            err_text = f"{e}\n\n{traceback.format_exc()}"
            self.error.emit(err_text)

    def _progress_cb(self, percent, filename):
        self.progress.emit(percent, filename)

    @staticmethod
    def _make_label(device_id, files):
        if device_id.startswith(('Lima_', 'W2_', 'Unknown_')):
            return device_id
        if files:
            parent = Path(files[0]).parent.name
            return parent
        return device_id


# =============================================================================
# КНОПКА-КРЕСТИК — квадратная, с SVG-иконкой по центру
# =============================================================================

class _CloseButton(QPushButton):
    """Квадратная кнопка-крестик 24×24 с SVG-иконкой."""

    SIZE = 24
    ICON_SIZE = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Удалить анализ")

        self.setIcon(_make_svg_icon(CLOSE_ICON_SVG, size=self.ICON_SIZE,
                                     color="#9ca3bf"))
        self.setIconSize(QSize(self.ICON_SIZE, self.ICON_SIZE))

        self.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 0px;
                margin: 0px;
                text-align: center;
            }
            QPushButton:hover {
                background: #ef4444;
            }
            QPushButton:pressed {
                background: #dc2626;
            }
        """)


# =============================================================================
# КАРТОЧКА АНАЛИЗА (кликабельная, с крестиком справа)
# =============================================================================

class AnalysisItemCard(QFrame):
    """Карточка одного выполненного анализа."""

    clicked = Signal(dict)
    remove_requested = Signal(dict)

    def __init__(self, analysis: dict, parent=None):
        super().__init__(parent)
        self.analysis = analysis

        self.setObjectName("analysisItemCard")
        self.setFixedHeight(48)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 10, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignVCenter)

        title_label = QLabel(analysis['title'])
        title_label.setStyleSheet("""
            font-weight: 600;
            font-size: 10pt;
            color: #e4e5f1;
            background: transparent;
        """)
        title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(title_label, stretch=1, alignment=Qt.AlignVCenter)

        is_open = (analysis['window'] is not None and
                   not analysis['window'].isHidden())
        status_text = "окно открыто" if is_open else "окно закрыто"
        status_color = "#10b981" if is_open else "#9ca3bf"
        self.status_label = QLabel(status_text)
        self.status_label.setStyleSheet(f"""
            font-size: 9pt;
            color: {status_color};
            background: transparent;
            padding-right: 8px;
        """)
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.status_label, alignment=Qt.AlignVCenter)

        self.close_btn = _CloseButton()
        self.close_btn.clicked.connect(self._on_remove_clicked)
        layout.addWidget(self.close_btn, 0, Qt.AlignVCenter | Qt.AlignRight)

        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet("""
            QFrame#analysisItemCard {
                background-color: #2a2b40;
                border: 1px solid #34354e;
                border-radius: 8px;
            }
            QFrame#analysisItemCard:hover {
                background-color: #353657;
                border: 1px solid #6366f1;
            }
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            widget_at_pos = self.childAt(event.position().toPoint())
            if not isinstance(widget_at_pos, QPushButton) and \
                    widget_at_pos is not self.close_btn:
                self.clicked.emit(self.analysis)
        super().mousePressEvent(event)

    def _on_remove_clicked(self):
        self.remove_requested.emit(self.analysis)


# =============================================================================
# СТРАНИЦА АНАЛИЗАТОРА
# =============================================================================

class ParserPage(QWidget):
    log_info = Signal(str)
    log_success = Signal(str)
    log_warning = Signal(str)
    log_error = Signal(str)
    progress_update = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.config = get_config()
        self.selected_files: list[str] = []
        self.worker: AnalysisWorker | None = None
        self.analyses_history: list[dict] = []
        self._next_analysis_id = 1
        self._build_ui()

    # ------------------------------------------------------------------
    # Утилита для создания заголовка с SVG-иконкой
    # ------------------------------------------------------------------

    def _make_header_row(self, icon_name: str, text: str,
                         icon_size: int = 28,
                         font_style: str = "pageTitle") -> QHBoxLayout:
        """Создаёт горизонтальный layout: SVG-иконка слева + текст справа."""
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

    def _make_card_header(self, icon_name: str, text: str) -> QHBoxLayout:
        """Шапка карточки: SVG-иконка + заголовок (меньше, чем pageTitle)."""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setAlignment(Qt.AlignVCenter)

        icon_label = QLabel()
        icon_label.setFixedSize(18, 18)
        icon_label.setPixmap(
            load_icon(icon_name, size=18,
                      color=ICON_COLOR_HOVER).pixmap(18, 18)
        )
        row.addWidget(icon_label)

        text_label = QLabel(text)
        text_label.setStyleSheet("font-size: 12pt; font-weight: 600;")
        row.addWidget(text_label)
        row.addStretch()
        return row

    def _make_subheader_row(self, icon_name: str, text: str,
                             size: int = 14) -> QHBoxLayout:
        """Подзаголовок внутри карточки: мелкая иконка + текст."""
        row = QHBoxLayout()
        row.setSpacing(6)
        row.setAlignment(Qt.AlignVCenter)
        row.setContentsMargins(0, 0, 0, 0)

        icon_label = QLabel()
        icon_label.setFixedSize(size, size)
        icon_label.setPixmap(
            load_icon(icon_name, size=size,
                      color=ICON_COLOR_NORMAL).pixmap(size, size)
        )
        row.addWidget(icon_label)

        text_label = QLabel(text)
        text_label.setStyleSheet("font-weight: 500; font-size: 9pt;")
        row.addWidget(text_label)
        row.addStretch()
        return row

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
        )

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(14)

        # Заголовок страницы: иконка parser.svg + текст
        layout.addLayout(
            self._make_header_row("parser.svg", "Анализатор логов")
        )

        layout.addWidget(self._build_new_analysis_card())
        layout.addWidget(self._build_load_analysis_card())
        layout.addWidget(self._build_open_analyses_card())
        layout.addStretch()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        scroll.setWidget(container)
        main_layout.addWidget(scroll)

    def _build_new_analysis_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(15, 12, 15, 12)
        card_layout.setSpacing(10)

        # Заголовок карточки: rocket + «Запуск нового анализа»
        card_layout.addLayout(
            self._make_card_header("rocket.svg", "Запуск нового анализа")
        )

        # Подзаголовок: folder + «Выбранные файлы:»
        card_layout.addLayout(
            self._make_subheader_row("folder.svg", "Выбранные файлы:")
        )

        row = QHBoxLayout()
        row.setSpacing(8)

        self.files_list = QListWidget()
        self.files_list.setMinimumHeight(200)
        row.addWidget(self.files_list, stretch=1)

        file_buttons = QVBoxLayout()
        file_buttons.setSpacing(4)

        add_files_btn = QPushButton("  Добавить файлы")
        add_files_btn.setObjectName("compactButton")
        add_files_btn.setIcon(load_icon("plus.svg", size=14))
        add_files_btn.setIconSize(QSize(14, 14))
        add_files_btn.setCursor(Qt.PointingHandCursor)
        add_files_btn.clicked.connect(self._on_add_files)
        file_buttons.addWidget(add_files_btn)

        scan_folder_btn = QPushButton("  Сканировать папку")
        scan_folder_btn.setObjectName("compactButton")
        scan_folder_btn.setIcon(load_icon("folder.svg", size=14))
        scan_folder_btn.setIconSize(QSize(14, 14))
        scan_folder_btn.setCursor(Qt.PointingHandCursor)
        scan_folder_btn.clicked.connect(self._on_scan_folder)
        file_buttons.addWidget(scan_folder_btn)

        remove_file_btn = QPushButton("  Удалить")
        remove_file_btn.setObjectName("compactButton")
        remove_file_btn.setIcon(load_icon("trash.svg", size=14))
        remove_file_btn.setIconSize(QSize(14, 14))
        remove_file_btn.setCursor(Qt.PointingHandCursor)
        remove_file_btn.clicked.connect(self._on_remove_file)
        file_buttons.addWidget(remove_file_btn)

        clear_files_btn = QPushButton("  Очистить")
        clear_files_btn.setObjectName("compactButton")
        clear_files_btn.setIcon(load_icon("broom.svg", size=14))
        clear_files_btn.setIconSize(QSize(14, 14))
        clear_files_btn.setCursor(Qt.PointingHandCursor)
        clear_files_btn.clicked.connect(self._on_clear_files)
        file_buttons.addWidget(clear_files_btn)

        file_buttons.addStretch()
        row.addLayout(file_buttons)
        card_layout.addLayout(row)

        # Отступ между списком файлов и кнопкой «Начать анализ»
        card_layout.addSpacing(15)

        # Primary-кнопка «Начать анализ» с иконкой ракеты
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.analyze_btn = QPushButton("  Начать анализ")
        self.analyze_btn.setMinimumWidth(180)
        self.analyze_btn.setCursor(Qt.PointingHandCursor)
        self.analyze_btn.setIcon(load_icon("rocket.svg", size=16,
                                           color="#ffffff"))
        self.analyze_btn.setIconSize(QSize(16, 16))
        self.analyze_btn.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.analyze_btn.clicked.connect(self._on_start_analysis)
        btn_row.addWidget(self.analyze_btn)
        btn_row.addStretch()
        card_layout.addLayout(btn_row)

        return card

    def _build_load_analysis_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(15, 12, 15, 12)
        card_layout.setSpacing(10)

        # Заголовок: folder + текст
        card_layout.addLayout(
            self._make_card_header("folder.svg",
                                    "Загрузить сохранённый анализ")
        )

        card_layout.addStretch()

        self.load_btn = QPushButton("  Выбрать .anl.gz")
        self.load_btn.setMinimumWidth(160)
        self.load_btn.setCursor(Qt.PointingHandCursor)
        self.load_btn.setIcon(load_icon("folder.svg", size=16,
                                        color="#ffffff"))
        self.load_btn.setIconSize(QSize(16, 16))
        self.load_btn.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.load_btn.clicked.connect(self._on_load_analysis)
        card_layout.addWidget(self.load_btn)

        return card

    def _build_open_analyses_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(15, 12, 15, 12)
        card_layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.addLayout(
            self._make_card_header("parser.svg", "Выполненные анализы")
        )
        clear_all_btn = QPushButton("  Очистить все")
        clear_all_btn.setObjectName("compactButton")
        clear_all_btn.setIcon(load_icon("broom.svg", size=14))
        clear_all_btn.setIconSize(QSize(14, 14))
        clear_all_btn.setCursor(Qt.PointingHandCursor)
        clear_all_btn.clicked.connect(self._on_clear_all_analyses)
        header_row.addWidget(clear_all_btn)
        card_layout.addLayout(header_row)

        self.analyses_container = QVBoxLayout()
        self.analyses_container.setContentsMargins(0, 0, 0, 0)
        self.analyses_container.setSpacing(6)
        card_layout.addLayout(self.analyses_container)

        # Заглушка «Нет анализов» — иконка info + текст
        self._add_no_analyses_placeholder()

        return card

    def _add_no_analyses_placeholder(self):
        """Добавляет в analyses_container виджет-заглушку."""
        placeholder = QWidget()
        placeholder_layout = QHBoxLayout(placeholder)
        placeholder_layout.setContentsMargins(0, 20, 0, 20)
        placeholder_layout.setSpacing(8)
        placeholder_layout.setAlignment(Qt.AlignCenter)

        info_icon = QLabel()
        info_icon.setFixedSize(16, 16)
        info_icon.setPixmap(
            load_icon("info.svg", size=16,
                      color=ICON_COLOR_MUTED).pixmap(16, 16)
        )
        placeholder_layout.addWidget(info_icon)

        text = QLabel(
            "Пока нет выполненных анализов. "
            "Запустите новый анализ или загрузите .anl.gz."
        )
        text.setStyleSheet(
            "color: #5b5f7a; font-style: italic;"
        )
        placeholder_layout.addWidget(text)

        self.no_analyses_placeholder = placeholder
        self.analyses_container.addWidget(placeholder)

    # ========================================================================
    # ФАЙЛЫ
    # ========================================================================

    def _on_add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите файлы логов", "",
            "Log files (*.log *.log.* *.log.*.gz *.gz);;"
            "Text files (*.txt);;All files (*)"
        )
        if not files:
            return
        added = 0
        for f in files:
            norm = str(Path(f).resolve())
            if not any(str(Path(p).resolve()) == norm for p in self.selected_files):
                self.selected_files.append(f)
                added += 1
        self._refresh_files_list()
        if added:
            self.log_info.emit(f"Добавлено файлов: {added}")

    def _on_scan_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с логами")
        if not folder:
            return
        folder_path = Path(folder)
        pattern = re.compile(
            r'devices_server\.log\.\d+$|skills-battle-frontend\.log\.\d+$'
        )
        found = []
        for fp in folder_path.iterdir():
            if not fp.is_file():
                continue
            name = fp.name
            if (pattern.match(name) or fp.suffix == '.log' or
                    fp.suffix == '.gz' or name.endswith('.log.gz')):
                found.append(str(fp))
        found.sort(key=lambda f: (0, int(re.search(r'\.(\d+)', Path(f).name).group(1)))
                   if re.search(r'\.(\d+)', Path(f).name) else (1, Path(f).name))
        added = 0
        existing_norm = {str(Path(p).resolve()) for p in self.selected_files}
        for f in found:
            norm = str(Path(f).resolve())
            if norm not in existing_norm:
                self.selected_files.append(f)
                existing_norm.add(norm)
                added += 1
        self._refresh_files_list()
        gz_count = sum(1 for f in self.selected_files if f.endswith('.gz'))
        msg = f"Найдено и добавлено файлов: {added}"
        if gz_count:
            msg += f" (из них {gz_count} .gz — читаются напрямую)"
        self.log_info.emit(msg)
        QMessageBox.information(self, "Сканирование", msg)

    def _on_remove_file(self):
        selected_items = self.files_list.selectedItems()
        if not selected_items:
            self.log_warning.emit("Выберите файл для удаления")
            return
        for item in selected_items:
            row = self.files_list.row(item)
            if 0 <= row < len(self.selected_files):
                del self.selected_files[row]
        self._refresh_files_list()

    def _on_clear_files(self):
        if not self.selected_files:
            return
        self.selected_files.clear()
        self._refresh_files_list()
        self.log_info.emit("Список файлов очищен")

    def _refresh_files_list(self):
        self.files_list.clear()
        for fp in self.selected_files:
            path = Path(fp)
            display = f"{path.name}   •   {path.parent.name}"
            if fp.endswith('.gz'):
                display += "   [gz]"
            item = QListWidgetItem(display)
            item.setToolTip(fp)
            self.files_list.addItem(item)

    # ========================================================================
    # ЗАПУСК АНАЛИЗА
    # ========================================================================

    def _on_start_analysis(self):
        if not self.selected_files:
            QMessageBox.warning(self, "Нет файлов",
                                "Сначала выберите файлы логов.")
            return
        files_by_device = self._group_files_by_device(self.selected_files)
        if not files_by_device:
            QMessageBox.warning(self, "Нет данных",
                                "Не удалось сгруппировать файлы для анализа.")
            return

        self.analyze_btn.setEnabled(False)
        self.load_btn.setEnabled(False)

        total_files = sum(len(f) for f in files_by_device.values())
        n_devices = len(files_by_device)
        self.log_info.emit(
            f"Старт анализа: {total_files} файлов, {n_devices} устройств"
        )

        self.worker = AnalysisWorker(files_by_device, parent=self)
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.device_done.connect(self._on_device_done)
        self.worker.finished_all.connect(self._on_analysis_finished)
        self.worker.error.connect(self._on_analysis_error)
        self.worker.start()

    def _group_files_by_device(self, files):
        result: dict[str, list[str]] = defaultdict(list)
        folder_pattern = re.compile(r'^(Lima|W2|Unknown)_[^_]+_\d{4}-\d{2}-\d{2}$')
        for fp in files:
            parent_name = Path(fp).parent.name
            if folder_pattern.match(parent_name):
                device_id = parent_name
            else:
                device_id = 'local'
            result[device_id].append(fp)
        return dict(result)

    @Slot(int, str)
    def _on_worker_progress(self, percent, filename):
        self.progress_update.emit(percent, f"{percent}% — {filename}")

    @Slot(str, dict)
    def _on_device_done(self, device_id, device_data):
        n_lima = len(device_data['lima'].sessions)
        n_w2 = len(device_data['w2_frontend'].sessions)
        n_ll = len(device_data['w2_lowlevel'].sessions)
        self.log_success.emit(
            f"Устройство {device_data['label']}: "
            f"Lima={n_lima}, W2={n_w2}, LL={n_ll}"
        )

    @Slot(dict)
    def _on_analysis_finished(self, devices_data):
                # ===== ВРЕМЕННАЯ ДИАГНОСТИКА ЭТАПА 2 =====
        for dev_id, data in devices_data.items():
            w2_front = data.get('w2_frontend')
            if not w2_front:
                continue
            print(f"\n[Этап 2 debug] Устройство: {dev_id}")
            print(f"  Cash events всего: "
                f"{len(getattr(w2_front, 'cash_events', []))}")
            for e in getattr(w2_front, 'cash_events', [])[:10]:
                print(f"  • {e.date_str()} {e.time_str()} "
                    f"{e.type_display()} "
                    f"| boxes={e.boxes_delta} "
                    f"| reject={e.reject_delta} "
                    f"| by_nominal={e.raw_by_nominal} "
                    f"| total=${e.total_cents/100:.2f}")
        # ===== конец диагностики =====
        self.analyze_btn.setEnabled(True)
        self.load_btn.setEnabled(True)
        self.progress_update.emit(100, "Анализ завершён")
        self.log_success.emit(f"Анализ завершён. Устройств: {len(devices_data)}")

        labels = [d.get('label', d_id) for d_id, d in devices_data.items()]
        if len(labels) == 1:
            title = labels[0]
        elif len(labels) <= 3:
            title = " / ".join(labels)
        else:
            title = f"Multi-device ({len(labels)} devices)"

        self._add_analysis_and_open_window(title, devices_data)

    @Slot(str)
    def _on_analysis_error(self, err_text):
        self.analyze_btn.setEnabled(True)
        self.load_btn.setEnabled(True)
        self.log_error.emit(f"Ошибка анализа: {err_text[:300]}")
        QMessageBox.critical(self, "Ошибка анализа", err_text)

    # ========================================================================
    # ПРИЁМ СТРИМОВ ОТ КОННЕКТОРА
    # ========================================================================

    def analyze_from_streams(self, device_id: str, streams_data: list,
                              device_label: str):
        if not streams_data:
            self.log_warning.emit(
                f"Нет данных для анализа от устройства {device_id}"
            )
            return

        try:
            self.log_info.emit(
                f"Старт анализа стримов от {device_label} "
                f"({len(streams_data)} файлов)"
            )

            analyzer = LogAnalyzer()
            w2_front = W2FrontendAnalyzer()
            w2_ll = W2LowLevelAnalyzer()

            for filename, text, log_type in streams_data:
                if log_type == 'frontend':
                    w2_front.parse_log_file(
                        io.StringIO(text), self._stream_progress_cb,
                        display_name=filename
                    )
                elif log_type == 'devices':
                    analyzer.parse_log_file(
                        io.StringIO(text), self._stream_progress_cb,
                        display_name=filename
                    )
                    w2_ll.parse_log_file(
                        io.StringIO(text), self._stream_progress_cb,
                        display_name=filename
                    )
                else:
                    analyzer.parse_log_file(
                        io.StringIO(text), self._stream_progress_cb,
                        display_name=filename
                    )
                    w2_front.parse_log_file(
                        io.StringIO(text), self._stream_progress_cb,
                        display_name=filename
                    )
                    w2_ll.parse_log_file(
                        io.StringIO(text), self._stream_progress_cb,
                        display_name=filename
                    )

            analyzer.build_sessions()
            w2_front.build_sessions()
            w2_ll.build_sessions()
            w2_ll.correlate_with_frontend(w2_front.sessions)

            data_key = device_label
            devices_data = {
                data_key: {
                    'device_id': data_key,
                    'label': data_key,
                    'files': [name for name, _, _ in streams_data],
                    'lima': analyzer,
                    'w2_frontend': w2_front,
                    'w2_lowlevel': w2_ll,
                }
            }

            self.progress_update.emit(100, "Анализ стрима завершён")

            n_lima = len(analyzer.sessions)
            n_w2 = len(w2_front.sessions)
            n_ll = len(w2_ll.sessions)
            self.log_success.emit(
                f"Стрим-анализ {device_label}: "
                f"Lima={n_lima}, W2={n_w2}, LL={n_ll}"
            )

            self._add_analysis_and_open_window(data_key, devices_data)

        except Exception as e:
            err = traceback.format_exc()
            self.log_error.emit(f"Ошибка анализа стримов: {e}")
            QMessageBox.critical(self, "Ошибка анализа стримов", err)

    def _stream_progress_cb(self, progress, filename):
        self.progress_update.emit(
            min(progress, 99), f"Стрим: {filename}"
        )

    # ========================================================================
    # УПРАВЛЕНИЕ АНАЛИЗАМИ И ОКНАМИ
    # ========================================================================

    def _add_analysis_and_open_window(self, title: str, devices_data: dict):
        analysis_id = f"analysis_{self._next_analysis_id}"
        self._next_analysis_id += 1

        analysis = {
            'id': analysis_id,
            'title': title,
            'created': datetime.now(),
            'devices_data': devices_data,
            'window': None,
        }
        self.analyses_history.append(analysis)
        self._open_window_for_analysis(analysis)
        self._refresh_analyses_list()

    def _open_window_for_analysis(self, analysis: dict):
        if analysis['window'] is not None and not analysis['window'].isHidden():
            analysis['window'].raise_()
            analysis['window'].activateWindow()
            return

        window = AnalysisResultWindow(
            analysis['devices_data'],
            title_suffix=analysis['title']
        )
        window.window_closed.connect(
            lambda win, a=analysis: self._on_window_closed(a, win)
        )
        analysis['window'] = window
        window.show()
        self._refresh_analyses_list()

    def _on_window_closed(self, analysis: dict, window: AnalysisResultWindow):
        analysis['window'] = None
        self._refresh_analyses_list()
        self.log_info.emit(f"Окно анализа «{analysis['title']}» закрыто")

    def _refresh_analyses_list(self):
        while self.analyses_container.count():
            item = self.analyses_container.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not self.analyses_history:
            self._add_no_analyses_placeholder()
            return

        for analysis in self.analyses_history:
            card = AnalysisItemCard(analysis)
            card.clicked.connect(self._open_window_for_analysis)
            card.remove_requested.connect(self._on_remove_analysis)
            self.analyses_container.addWidget(card)

    def _on_remove_analysis(self, analysis: dict):
        reply = QMessageBox.question(
            self, "Удалить анализ",
            f"Удалить анализ «{analysis['title']}»?\n\n"
            f"Данные будут потеряны. Если окно открыто — оно закроется.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        if analysis['window'] is not None:
            try:
                analysis['window'].close()
            except Exception:
                pass

        self.analyses_history.remove(analysis)
        self._refresh_analyses_list()
        self.log_info.emit(f"Анализ «{analysis['title']}» удалён")

    def _on_clear_all_analyses(self):
        if not self.analyses_history:
            return
        reply = QMessageBox.question(
            self, "Очистить все анализы",
            f"Удалить все {len(self.analyses_history)} анализов?\n\n"
            f"Все данные будут потеряны, окна закроются.",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        for analysis in self.analyses_history:
            if analysis['window'] is not None:
                try:
                    analysis['window'].close()
                except Exception:
                    pass
        self.analyses_history.clear()
        self._refresh_analyses_list()
        self.log_info.emit("Все анализы очищены")

    # ========================================================================
    # ЗАГРУЗКА .anl.gz
    # ========================================================================

    def _on_load_analysis(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл анализа", "",
            "Analysis files (*.anl.gz);;All files (*)"
        )
        if not fp:
            return
        try:
            analyzer, w2_analyzer, w2_lowlevel, metadata = \
                AnalysisSerializer.load(fp)
            device_id = Path(fp).stem.replace('.anl', '')
            devices_data = {
                device_id: {
                    'device_id': device_id,
                    'label': device_id,
                    'files': metadata.get('source_files', []),
                    'lima': analyzer,
                    'w2_frontend': w2_analyzer,
                    'w2_lowlevel': w2_lowlevel,
                    'metadata': metadata,
                }
            }
            title = device_id
            self.log_success.emit(
                f"Загружен: {device_id} "
                f"(сохранён: {metadata.get('saved_at', '?')})"
            )
            self._add_analysis_and_open_window(title, devices_data)
        except Exception as e:
            self.log_error.emit(f"Ошибка загрузки: {e}")
            QMessageBox.critical(self, "Ошибка загрузки",
                                 traceback.format_exc())