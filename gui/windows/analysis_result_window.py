"""Отдельное окно с результатами анализа.

Открывается при каждом запуске нового анализа или загрузке .anl.gz.
Содержит вкладки по устройствам (если их несколько), в каждой — только
те внутренние вкладки анализа, в которых реально есть данные.
"""

import os
import traceback
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QBrush, QColor, QCloseEvent, QGuiApplication
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTabWidget, QTextEdit, QFileDialog, QMessageBox,
    QFrame, QTreeWidget, QTreeWidgetItem, QHeaderView, QRadioButton,
)

from version import APP_NAME
from core.constants import (
    ALL_DENOMINATIONS, DENOM_MAP, BNV_EVENT_NAMES, W2_STATUS_DISPLAY,
)
from parsers.lima_parser import LogAnalyzer
from parsers.w2_frontend_parser import W2FrontendAnalyzer
from parsers.w2_lowlevel_parser import W2LowLevelAnalyzer
from parsers.serializer import AnalysisSerializer


# Палитра для QTreeWidgetItem
COLOR_SUCCESS = QColor("#10b981")
COLOR_WARNING = QColor("#f59e0b")
COLOR_DANGER = QColor("#ef4444")
COLOR_INFO = QColor("#3b82f6")
COLOR_MUTED = QColor("#9ca3bf")
COLOR_PURPLE = QColor("#8b5cf6")
COLOR_EVENT = QColor("#c9cad8")
COLOR_SUSPICIOUS = QColor("#eab308")
COLOR_DATE_BG = QColor("#2d2e45")
COLOR_LIGHT_BLUE = QColor("#0ea5e9")

# Фоновый цвет окна результатов — совпадает с bg_window из темы
BG_WINDOW_COLOR = "#0f1119"


# =============================================================================
# ПРАВИЛА ПОКАЗА ВКЛАДОК — создаём вкладку только если есть реальные данные
# =============================================================================

def _has_lima_sessions(device_data: dict) -> bool:
    analyzer: LogAnalyzer = device_data.get('lima')
    return bool(analyzer and len(analyzer.sessions) > 0)


def _has_bnv_events(device_data: dict) -> bool:
    analyzer: LogAnalyzer = device_data.get('lima')
    return bool(analyzer and len(analyzer.bnv_events) > 0)


def _has_w2_frontend(device_data: dict) -> bool:
    w2_front: W2FrontendAnalyzer = device_data.get('w2_frontend')
    return bool(w2_front and len(w2_front.sessions) > 0)


def _has_w2_lowlevel(device_data: dict) -> bool:
    w2_ll: W2LowLevelAnalyzer = device_data.get('w2_lowlevel')
    return bool(w2_ll and len(w2_ll.sessions) > 0)


def _has_health(device_data: dict) -> bool:
    w2_ll: W2LowLevelAnalyzer = device_data.get('w2_lowlevel')
    if not w2_ll:
        return False
    return bool(len(w2_ll.lcdm_health_changes) > 0 or len(w2_ll.lcdm_health) > 0)


# Стиль для QTabWidget — убирает серую полосу/рамку и красит фон в тёмный
_PANE_TRANSPARENT_STYLE = """
    QTabWidget {
        background-color: transparent;
        border: none;
    }
    QTabWidget > QTabBar {
        background-color: transparent;
        border: none;
        border-top: 0px;
        border-bottom: 0px;
    }
    QTabWidget::pane {
        background-color: transparent;
        border: none;
        border-radius: 0;
        top: 0px;
    }
    QTabBar::tab {
        border: none;
    }
"""


# =============================================================================
# ОСНОВНОЕ ОКНО
# =============================================================================

class AnalysisResultWindow(QMainWindow):
    """Отдельное окно с результатами анализа.

    Структура:
      - Если устройств несколько — верхние вкладки по устройствам.
      - Внутри каждой — только те внутренние вкладки, где есть данные.
      - Внизу — кнопки сохранения и закрытия окна.

    Размер окна: 90% от доступной геометрии экрана, центрировано.
    """

    window_closed = Signal(object)

    def __init__(self, devices_data: dict, title_suffix: str = "",
                 parent=None):
        super().__init__(parent)
        self.devices_data = devices_data
        self._title_suffix = title_suffix

        self.setWindowFlags(Qt.Window)

        # Размер окна — 90% от доступного экрана (без task bar).
        screen = self._get_available_screen_geometry()
        if screen is not None:
            width = max(900, int(screen.width() * 0.9))
            height = max(600, int(screen.height() * 0.9))
            self.resize(width, height)
            self.move(
                screen.x() + (screen.width() - width) // 2,
                screen.y() + (screen.height() - height) // 2
            )
        else:
            self.resize(1600, 900)

        self.setMinimumSize(900, 600)

        self._set_window_title()
        self._build_ui()

    @staticmethod
    def _get_available_screen_geometry():
        """Возвращает QRect с доступной геометрией экрана (без task bar).
        При ошибке возвращает None."""
        try:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return None
            return screen.availableGeometry()
        except Exception:
            return None

    def _set_window_title(self):
        if self._title_suffix:
            name = self._title_suffix
        else:
            labels = [d.get('label', d_id) for d_id, d in self.devices_data.items()]
            if len(labels) == 1:
                name = labels[0]
            elif len(labels) <= 3:
                name = " / ".join(labels)
            else:
                name = f"Multi-device analysis ({len(labels)} devices)"
        self.setWindowTitle(f"📊 {name} — {APP_NAME}")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Красим само окно в тёмный фон — убирает серую полосу под заголовком
        self.setStyleSheet(
            f"QMainWindow {{ background-color: {BG_WINDOW_COLOR}; }}"
        )

        central = QWidget()
        central.setObjectName("centralWidget")
        central.setStyleSheet(
            f"QWidget#centralWidget {{ background-color: {BG_WINDOW_COLOR}; }}"
        )
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(15, 35, 15, 15)
        layout.setSpacing(10)

        if len(self.devices_data) == 1:
            device_id, data = next(iter(self.devices_data.items()))
            inner_tabs = self._build_device_tabs(device_id, data)
            inner_tabs.setStyleSheet(_PANE_TRANSPARENT_STYLE)
            layout.addWidget(inner_tabs, stretch=1)
        else:
            self.device_tabs = QTabWidget()
            self.device_tabs.setDocumentMode(True)
            self.device_tabs.tabBar().setDrawBase(False)
            self.device_tabs.tabBar().setAutoFillBackground(True)
            self.device_tabs.setStyleSheet(_PANE_TRANSPARENT_STYLE)
            for device_id, data in sorted(self.devices_data.items()):
                inner_tabs = self._build_device_tabs(device_id, data)
                inner_tabs.setStyleSheet(_PANE_TRANSPARENT_STYLE)
                label = data.get('label', device_id)
                self.device_tabs.addTab(inner_tabs, label)
            layout.addWidget(self.device_tabs, stretch=1)

        # ===== Нижние кнопки =====
        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)

        self.save_btn = QPushButton("💾 Сохранить .anl.gz")
        self.save_btn.setObjectName("compactButton")
        self.save_btn.clicked.connect(self._on_save_analysis)
        actions_row.addWidget(self.save_btn)

        actions_row.addStretch()

        close_btn = QPushButton("Закрыть окно")
        close_btn.setObjectName("compactButton")
        close_btn.clicked.connect(self.close)
        actions_row.addWidget(close_btn)

        layout.addLayout(actions_row)

    def _build_device_tabs(self, device_id: str, data: dict) -> QTabWidget:
        """Внутренние вкладки для одного устройства."""
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.tabBar().setDrawBase(False)

        stats_tab = self._build_stats_tab(data)
        tabs.addTab(stats_tab, "📊 Статистика")

        if _has_lima_sessions(data):
            lifecycle_tab = self._build_lifecycle_tab(data)
            tabs.addTab(lifecycle_tab, "🔍 ЖЦ Купюр")

        if _has_bnv_events(data):
            bnv_tab = self._build_bnv_tab(data)
            tabs.addTab(bnv_tab, "🔧 BNV")

        if _has_w2_frontend(data):
            w2_tab = self._build_w2_tab(data)
            tabs.addTab(w2_tab, "💸 Выдачи W2")

        if _has_w2_lowlevel(data):
            w2ll_tab = self._build_w2ll_tab(data)
            tabs.addTab(w2ll_tab, "⚙️ W2 Low-Level")

        if _has_health(data):
            health_tab = self._build_health_tab(data)
            tabs.addTab(health_tab, "📡 Health")

        return tabs

    # ------------------------------------------------------------------
    # ВКЛАДКА «📊 СТАТИСТИКА»
    # ------------------------------------------------------------------

    def _build_stats_tab(self, data: dict) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)

        stats_text = QTextEdit()
        stats_text.setReadOnly(True)
        stats_text.setFont(QFont("Consolas", 10))
        stats_text.setStyleSheet("""
            QTextEdit {
                background-color: #2d2e45;
                color: #e4e5f1;
                border: 1px solid #34354e;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        stats_text.setPlainText(self._generate_stats_text(data))
        layout.addWidget(stats_text)
        return tab

    def _generate_stats_text(self, data: dict) -> str:
        """Собирает текстовый отчёт, пропуская разделы без данных."""
        try:
            analyzer: LogAnalyzer = data.get('lima')
            w2_front: W2FrontendAnalyzer = data.get('w2_frontend')
            w2_ll: W2LowLevelAnalyzer = data.get('w2_lowlevel')

            lines: list[str] = []
            label = data.get('label', 'unknown')
            lines.append("=" * 70)
            lines.append(f"  📊 СТАТИСТИКА — {label}")
            lines.append("=" * 70)
            lines.append("")

            if analyzer and (len(analyzer.sessions) > 0 or
                             len(analyzer.collections) > 0 or
                             len(analyzer.bnv_events) > 0):
                (stats, total_count, total_sum, lines_found, lines_parsed,
                 debug_info, sessions, debug_stacked,
                 between_stats, between_count, between_sum, first_coll, last_coll,
                 all_collections, debug_collections,
                 bnv_events_count, stats_by_device, device_models,
                 bnv_events_by_type) = analyzer.get_stats()

                lines.append("━" * 70)
                lines.append("  📥 LIMA (приём купюр)")
                lines.append("━" * 70)
                lines.append("")

                if device_models:
                    lines.append(f"🖥️  Купюроприёмники: {len(device_models)}")
                    for serial, info in sorted(device_models.items()):
                        lines.append(f"   • {info['model']:<20} SN: {serial}")
                    lines.append("")

                if all_collections:
                    lines.append(f"📦 Инкассации: {len(all_collections)}")
                    for i, c in enumerate(
                            sorted(all_collections, key=lambda c: c['dt']), 1):
                        lines.append(f"   {i}. {c['date_str']} {c['time_str']}")
                        for denom in sorted(c['notes'].keys()):
                            lines.append(f"      ${denom} x {c['notes'][denom]}")
                    lines.append("")

                if sessions:
                    status_counts = defaultdict(int)
                    for s in sessions:
                        status_counts[s.status] += 1
                    incomplete_total = (status_counts.get('INCOMPLETE', 0) +
                                        status_counts.get('INSERTED', 0) +
                                        status_counts.get('STACKING', 0))
                    lines.append(f"📊 Сессий (купюр): {len(sessions)}")
                    lines.append(f"   ✅ ПРИНЯТО:   {status_counts.get('STACKED', 0)}")
                    lines.append(f"   ❌ ОТКЛОНЕНО: {status_counts.get('REJECTED', 0)}")
                    lines.append(f"   ⛔ ОШИБКА:    {status_counts.get('ERROR', 0)}")
                    lines.append(f"   ⚠️  НЕЗАВЕРШ.: {incomplete_total}")
                    lines.append(f"   Принято купюр: {total_count}, "
                                 f"сумма: ${total_sum:.2f}")
                    lines.append("")

                if bnv_events_by_type:
                    lines.append("🔧 События BNV:")
                    for vt in sorted(bnv_events_by_type.keys()):
                        events_map = bnv_events_by_type[vt]
                        total_evs = sum(events_map.values())
                        lines.append(f"   📡 {vt} — всего: {total_evs}")
                        for en, cnt in sorted(events_map.items(),
                                              key=lambda x: -x[1]):
                            display = BNV_EVENT_NAMES.get(en, en)
                            lines.append(f"      {display:<42} {cnt:>4}")
                    lines.append("")

            if w2_front and len(w2_front.sessions) > 0:
                w2_stats = w2_front.get_stats()
                lines.append("━" * 70)
                lines.append("  💸 ВЫДАЧИ W2 (Frontend)")
                lines.append("━" * 70)
                lines.append(f"Всего выдач: {w2_stats['total']}")
                sc = w2_stats['status_counts']
                for key, display in [
                    ('OK', '✅ ВЫДАНО'), ('PARTIAL', '⚠️ ЧАСТИЧНО'),
                    ('FAILED_JAM', '⛔ JAM'), ('FAILED_EMPTY', '⛔ EMPTY'),
                    ('FAILED_TIMEOUT', '⛔ TIMEOUT'),
                    ('FAILED_HARDWARE', '⛔ HARDWARE'),
                    ('FAILED_COUPON', '❌ КУПОН'),
                    ('INCOMPLETE', '⏳ НЕЗАВЕРШ.'),
                ]:
                    cnt = sc.get(key, 0)
                    if cnt:
                        lines.append(f"   {display:<20} {cnt}")
                lines.append(f"   Запрошено: "
                             f"${w2_stats['total_requested_cents']/100:.2f}")
                lines.append(f"   Выдано:    "
                             f"${w2_stats['total_dispensed_cents']/100:.2f}")
                if w2_stats.get('suspicious_total', 0) > 0:
                    lines.append(f"   ⚠️ Подозрительных склеек: "
                                 f"{w2_stats['suspicious_total']}")
                lines.append("")

            if w2_ll and len(w2_ll.sessions) > 0:
                ll_stats = w2_ll.get_stats()
                lines.append("━" * 70)
                lines.append("  ⚙️ ВЫДАЧИ W2 (Low-Level)")
                lines.append("━" * 70)
                lines.append(f"Всего LL-сессий: {ll_stats['total']}")
                for m, cnt in sorted(ll_stats['by_model'].items()):
                    lines.append(f"   {m}: {cnt}")
                if ll_stats.get('lcdm_codes_count'):
                    lines.append("")
                    lines.append("Коды ошибок LCDM:")
                    for code, cnt in sorted(
                            ll_stats['lcdm_codes_count'].items(),
                            key=lambda x: -x[1]):
                        lines.append(f"   {code:<50} × {cnt}")
                lines.append("")

            if len(lines) <= 4:
                lines.append("ℹ️ Нет данных для этого устройства")

            return "\n".join(lines)
        except Exception as e:
            return f"ОШИБКА:\n{e}\n\n{traceback.format_exc()}"

    # ------------------------------------------------------------------
    # ВКЛАДКА «🔍 ЖЦ КУПЮР»
    # ------------------------------------------------------------------

    def _build_lifecycle_tab(self, data: dict) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)

        for text, status in [
            ("🔄 Все", None),
            ("✅ Принятые", 'STACKED'),
            ("❌ Отклонённые", 'REJECTED'),
            ("⛔ Ошибки", 'ERROR'),
            ("⚠️ Незавершённые", 'INCOMPLETE'),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("compactButton")
            btn.clicked.connect(
                lambda _, s=status, d=data, t=tab:
                    self._refresh_lifecycle(t, d, filter_status=s)
            )
            filter_row.addWidget(btn)
        filter_row.addStretch()

        filter_row.addWidget(QLabel("Модель:"))
        model_combo = QComboBox()
        model_combo.addItem("Все")
        model_combo.setMinimumWidth(200)
        analyzer: LogAnalyzer = data['lima']
        models = sorted({s.device_model for s in analyzer.sessions if s.device_model})
        for m in models:
            model_combo.addItem(m)
        model_combo.currentIndexChanged.connect(
            lambda: self._refresh_lifecycle(tab, data)
        )
        tab.model_combo = model_combo
        filter_row.addWidget(model_combo)

        layout.addLayout(filter_row)

        tree = QTreeWidget()
        tree.setColumnCount(6)
        tree.setHeaderLabels([
            "Дата / Сессия", "Время", "Статус / Событие",
            "Номинал", "Устройство", "Описание / Детали"
        ])
        self._setup_tree_header(tree, [420, 85, 180, 90, 160])
        layout.addWidget(tree, stretch=1)
        tab.tree = tree

        self._refresh_lifecycle(tab, data)
        return tab

    def _refresh_lifecycle(self, tab, data, filter_status=None):
        tree: QTreeWidget = tab.tree
        tree.clear()
        analyzer: LogAnalyzer = data['lima']
        sessions = list(analyzer.sessions)

        incomplete_statuses = {'INCOMPLETE', 'INSERTED', 'STACKING'}
        model_filter = tab.model_combo.currentText()
        if model_filter and model_filter != 'Все':
            sessions = [s for s in sessions if s.device_model == model_filter]

        by_date = defaultdict(list)
        for s in sessions:
            by_date[s.first_date or "Неизвестно"].append(s)

        for date in sorted(by_date.keys()):
            sess_list = by_date[date]
            if filter_status:
                if filter_status == 'INCOMPLETE':
                    sess_list = [s for s in sess_list
                                 if s.status in incomplete_statuses]
                else:
                    sess_list = [s for s in sess_list if s.status == filter_status]
                if not sess_list:
                    continue
            stacked = sum(1 for s in sess_list if s.status == 'STACKED')
            total = len(sess_list)
            amount = sum(s.get_display_denom() for s in sess_list
                         if s.status == 'STACKED')

            date_item = QTreeWidgetItem([
                f'📅 {date}', '', f'{stacked}/{total} принято',
                f'${amount:.0f}', '', ''
            ])
            self._apply_date_style(date_item)
            tree.addTopLevelItem(date_item)

            for s in sorted(sess_list, key=lambda x: x.start_ts):
                denom = s.get_display_denom()
                curr = s.currency or 'USD'
                if s.status == 'STACKED':
                    status_txt, color = '✅ ПРИНЯТА', COLOR_SUCCESS
                elif s.status == 'REJECTED':
                    status_txt, color = '❌ ОТКЛОНЕНА', COLOR_DANGER
                elif s.status == 'ERROR':
                    reason = s.error_reason or 'ошибка'
                    status_txt, color = f'⛔ ОШИБКА ({reason})', COLOR_DANGER
                else:
                    status_txt, color = '⚠️ НЕЗАВЕРШЕНА', COLOR_WARNING

                sid = f"#{s.session_id + 1}"
                uuid_short = (s.uuid[:18] + '…') if s.uuid and len(s.uuid) > 18 \
                    else (s.uuid or 'N/A')
                model_display = s.device_model or '?'
                if s.device_serial and s.device_serial != 'N/A':
                    model_display = f'{model_display} ({s.device_serial})'

                bill_item = QTreeWidgetItem([
                    f'{sid} 🆔 {uuid_short}', s.first_time, status_txt,
                    f'${denom:.0f} {curr}' if denom else '-',
                    model_display, f'{len(s.events)} событий'
                ])
                for col in range(6):
                    bill_item.setForeground(col, QBrush(color))
                date_item.addChild(bill_item)
                bill_item.setExpanded(s.status not in ('STACKED', 'REJECTED'))

                for ev in s.events:
                    is_bnv = ev.get('is_bnv', False)
                    ev_type_short = ev['event_type'].replace(
                        'DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_', ''
                    ).replace('BNV_', '[BNV] ')
                    ev_denom = (DENOM_MAP.get(ev['banknote_raw'],
                                              ev['banknote_raw']/100)
                                if ev.get('banknote_raw') else 0)
                    ev_item = QTreeWidgetItem([
                        f'  └─ {ev.get("time", "?")}', ev.get('time', '?'),
                        ev_type_short,
                        f'${ev_denom:.0f}' if ev_denom else '-',
                        ev.get('device_model') or '',
                        (ev.get('description') or '')[:80]
                    ])
                    ev_color = COLOR_DANGER if is_bnv else COLOR_MUTED
                    for col in range(6):
                        ev_item.setForeground(col, QBrush(ev_color))
                    bill_item.addChild(ev_item)

    # ------------------------------------------------------------------
    # ВКЛАДКА «🔧 BNV»
    # ------------------------------------------------------------------

    def _build_bnv_tab(self, data: dict) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        all_btn = QPushButton("🔄 Все события")
        all_btn.setObjectName("compactButton")
        all_btn.clicked.connect(
            lambda: self._refresh_bnv(tab, data, filter_warn_only=False)
        )
        filter_row.addWidget(all_btn)
        warn_btn = QPushButton("⚠️ Только тревоги")
        warn_btn.setObjectName("compactButton")
        warn_btn.clicked.connect(
            lambda: self._refresh_bnv(tab, data, filter_warn_only=True)
        )
        filter_row.addWidget(warn_btn)
        filter_row.addStretch()

        tab.info_label = QLabel("Событий: 0")
        tab.info_label.setStyleSheet("color: #9ca3bf;")
        filter_row.addWidget(tab.info_label)
        layout.addLayout(filter_row)

        tree = QTreeWidget()
        tree.setColumnCount(5)
        tree.setHeaderLabels([
            "Дата", "Время", "Валидатор", "Событие BNV", "Описание"
        ])
        self._setup_tree_header(tree, [200, 85, 110, 180])
        layout.addWidget(tree, stretch=1)
        tab.tree = tree

        self._refresh_bnv(tab, data)
        return tab

    def _refresh_bnv(self, tab, data, filter_warn_only=False):
        tree: QTreeWidget = tab.tree
        tree.clear()
        analyzer: LogAnalyzer = data['lima']
        events = list(analyzer.bnv_events)

        if filter_warn_only:
            warn_keywords = ['Jam', 'Fraud', 'Reject', 'Full',
                             'Disable', 'Unsafe', 'Error']
            events = [e for e in events
                      if any(k in e['description'] for k in warn_keywords)]
        tab.info_label.setText(
            f"Событий: {len(events)}" +
            (" (только тревоги)" if filter_warn_only else "")
        )
        if not events:
            return

        by_date = defaultdict(list)
        for ev in events:
            by_date[ev.get('date') or "Неизвестно"].append(ev)

        for date in sorted(by_date.keys()):
            day_events = by_date[date]
            date_item = QTreeWidgetItem([
                f'📅 {date}', '', '', f'{len(day_events)} событий', ''
            ])
            self._apply_date_style(date_item)
            tree.addTopLevelItem(date_item)

            for ev in sorted(day_events, key=lambda x: x['timestamp']):
                ev_name = ev['description']
                display = BNV_EVENT_NAMES.get(ev_name, ev_name)
                validator = ev.get('validator_type') or '?'
                if any(x in ev_name for x in ['Jam', 'Fraud', 'Reject', 'Full']):
                    color = COLOR_DANGER
                elif any(x in ev_name for x in ['Disable', 'Reset', 'Unsafe']):
                    color = COLOR_WARNING
                elif 'Stacked' in ev_name or 'Read' in ev_name:
                    color = COLOR_SUCCESS
                else:
                    color = COLOR_INFO
                ev_item = QTreeWidgetItem([
                    f'  └─ {ev["time"]}', ev['time'], validator,
                    ev_name, display
                ])
                for col in range(5):
                    ev_item.setForeground(col, QBrush(color))
                date_item.addChild(ev_item)

    # ------------------------------------------------------------------
    # ВКЛАДКА «💸 ВЫДАЧИ W2»
    # ------------------------------------------------------------------

    def _build_w2_tab(self, data: dict) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        for text, status in [
            ("🔄 Все", None),
            ("✅ Успешные", 'OK'),
            ("⚠️ Частичные", 'PARTIAL'),
            ("⛔ Ошибки", 'FAILED_ANY'),
            ("⏳ Незавершённые", 'INCOMPLETE'),
            ("⚠️ Подозрительные склейки", 'SUSPICIOUS'),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("compactButton")
            btn.clicked.connect(
                lambda _, s=status: self._refresh_w2(tab, data, filter_status=s)
            )
            filter_row.addWidget(btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Диспенсер:"))
        device_combo = QComboBox()
        device_combo.addItem("Все")
        device_combo.setMinimumWidth(300)
        w2_front: W2FrontendAnalyzer = data['w2_frontend']
        devs = set()
        for s in w2_front.sessions:
            if s.dispenser_device_id:
                label = f"{s.dispenser_model or '?'} ({s.dispenser_device_id})"
                devs.add(label)
        for d in sorted(devs):
            device_combo.addItem(d)
        device_combo.currentIndexChanged.connect(
            lambda: self._refresh_w2(tab, data)
        )
        tab.device_combo = device_combo
        row2.addWidget(device_combo)
        row2.addStretch()
        tab.info_label = QLabel("Выдач: 0")
        tab.info_label.setStyleSheet("font-weight: 600; color: #9ca3bf;")
        row2.addWidget(tab.info_label)
        layout.addLayout(row2)

        tree = QTreeWidget()
        tree.setColumnCount(8)
        tree.setHeaderLabels([
            "Дата / Сессия", "Время", "Логин / Купон", "Запрошено",
            "Выдано", "Статус", "Диспенсер", "Детали"
        ])
        self._setup_tree_header(tree, [420, 90, 140, 110, 110, 320, 240])
        layout.addWidget(tree, stretch=1)
        tab.tree = tree

        self._refresh_w2(tab, data)
        return tab

    def _refresh_w2(self, tab, data, filter_status=None):
        tree: QTreeWidget = tab.tree
        tree.clear()
        w2_front: W2FrontendAnalyzer = data['w2_frontend']
        sessions = list(w2_front.sessions)

        if filter_status:
            if filter_status == 'FAILED_ANY':
                sessions = [s for s in sessions if s.status.startswith('FAILED')]
            elif filter_status == 'SUSPICIOUS':
                sessions = [s for s in sessions if s.has_suspicious_match()]
            else:
                sessions = [s for s in sessions if s.status == filter_status]

        dev_filter = tab.device_combo.currentText()
        if dev_filter and dev_filter != 'Все':
            sessions = [
                s for s in sessions
                if s.dispenser_device_id and
                   dev_filter.endswith(f"({s.dispenser_device_id})")
            ]

        total_disp = sum(s.dispensed_amount for s in sessions)
        total_req = sum(s.requested_amount for s in sessions)
        suspicious_shown = sum(1 for s in sessions if s.has_suspicious_match())
        info_text = (f"Выдач: {len(sessions)}   |   "
                     f"Запрошено: ${total_req/100:.2f}   |   "
                     f"Выдано: ${total_disp/100:.2f}")
        if suspicious_shown > 0:
            info_text += f"   |   ⚠️ Подозрительных: {suspicious_shown}"
        tab.info_label.setText(info_text)
        if not sessions:
            return

        by_date = defaultdict(list)
        for s in sessions:
            by_date[s.start_date_str or "Неизвестно"].append(s)

        for date in sorted(by_date.keys()):
            sess_list = by_date[date]
            ok_count = sum(1 for s in sess_list if s.status == 'OK')
            total = len(sess_list)
            day_sum = sum(s.dispensed_amount for s in sess_list
                          if s.status == 'OK') / 100
            date_item = QTreeWidgetItem([
                f'📅 {date}', '', f'{ok_count}/{total} ok', '',
                f'${day_sum:.2f}', '', '', ''
            ])
            self._apply_date_style(date_item)
            tree.addTopLevelItem(date_item)

            for i, s in enumerate(sorted(sess_list,
                                          key=lambda x: x.start_dt or datetime.min),
                                  1):
                self._add_w2_session_item(date_item, i, s)

    def _add_w2_session_item(self, parent, idx, s):
        status_txt = W2_STATUS_DISPLAY.get(s.status, s.status)
        if s.error_detail and s.status.startswith('FAILED') and \
                s.status != 'FAILED_COUPON':
            status_txt = f"{status_txt} ({s.error_detail})"
        elif s.status == 'FAILED_COUPON' and s.payout_coupon_status and \
                s.payout_coupon_status != 'ok':
            status_txt = f"{status_txt} ({s.payout_coupon_status})"

        if s.status == 'OK':
            color = COLOR_SUCCESS
        elif s.status == 'PARTIAL':
            color = COLOR_WARNING
        elif s.status.startswith('FAILED'):
            color = COLOR_DANGER
        elif s.status == 'INCOMPLETE':
            color = COLOR_MUTED
        else:
            color = COLOR_EVENT

        trx_short = s.trx[:8] + '…' if s.trx else '?'
        login_display = s.user_login or s.coupon or '?'
        device_display = '?'
        if s.dispenser_device_id:
            device_display = s.dispenser_device_id
            if s.dispenser_model:
                device_display = (f"{s.dispenser_model} "
                                  f"({s.dispenser_device_id[:20]}…)")

        details = []
        if s.commands:
            details.append(f"{len(s.commands)} команд")
        if s.retry_count:
            details.append(f"retry×{s.retry_count}")
        if s.recovery_coupon:
            details.append(f"возврат-купон: {s.recovery_coupon}")
        if s.lowlevel_sessions:
            n = len(s.lowlevel_sessions)
            details.append("🔗 склейка с железом" if n == 1
                           else f"🔗 склейка с железом × {n}")
        if s.has_suspicious_match():
            details.append("⚠️ склейка?")
        details_str = ", ".join(details) if details else f"{len(s.events)} событий"

        sess_item = QTreeWidgetItem([
            f'#{idx} trx:{trx_short}', s.start_time_str, login_display,
            f'${s.requested_amount/100:.2f}',
            f'${s.dispensed_amount/100:.2f}',
            status_txt, device_display, details_str
        ])
        for col in range(8):
            sess_item.setForeground(col, QBrush(color))
        parent.addChild(sess_item)
        sess_item.setExpanded(s.status != 'OK' or s.has_suspicious_match())

        fe_item = QTreeWidgetItem([
            '  🖥️ Фронт (что видел клиент)', '', '', '', '', '', '',
            'запрос, события, кассеты'
        ])
        for col in range(8):
            fe_item.setForeground(col, QBrush(COLOR_INFO))
        sess_item.addChild(fe_item)
        fe_item.setExpanded(True)

        if s.commands:
            cmd_item = QTreeWidgetItem([
                '    📝 Запрошенные команды', '', '', '', '', '', '',
                f'{len(s.commands)} шт.'
            ])
            fe_item.addChild(cmd_item)
            for ci, cmd in enumerate(s.commands, 1):
                QTreeWidgetItem(cmd_item, [
                    f'       └─ #{ci}', '', '',
                    f"bank {cmd.get('bankIndex')}",
                    f"{cmd.get('notesAmount')} шт.", '',
                    cmd.get('deviceId', '')[:30], ''
                ])

        if s.cassette_snapshot:
            snap_str = ", ".join(f"${nom}×{cnt}"
                                 for nom, cnt in s.cassette_snapshot)
            QTreeWidgetItem(fe_item, [
                '    🏦 Snapshot кассет', '', '', '', '', '', '', snap_str
            ])

        if s.dispensed_events:
            evt_item = QTreeWidgetItem([
                '    📨 События выдачи (с фронта)', '', '', '', '', '', '',
                f'{len(s.dispensed_events)} шт.'
            ])
            fe_item.addChild(evt_item)
            for di, de in enumerate(s.dispensed_events, 1):
                has_error = bool(de.get('statusDetail'))
                req = de.get('notesAmountRequested', '?')
                disp = de.get('notesAmountDispensed', 0)
                bank = de.get('bankIndex', '?')
                error_text = de.get('statusDetail', '')
                label = f"       └─ Фронт-событие #{di}"
                if has_error:
                    label = f"       └─ ⛔ Фронт-событие #{di} ERROR"
                de_item = QTreeWidgetItem([
                    label, '', '', f"bank {bank}", f"{disp}/{req}",
                    error_text[:60] if error_text else 'ok', '',
                    f"CHK={de.get('notesAmountChkSensor', '?')}"
                ])
                if has_error:
                    for col in range(8):
                        de_item.setForeground(col, QBrush(COLOR_DANGER))
                evt_item.addChild(de_item)

        if s.boxes:
            grouped = defaultdict(lambda: {0: 0, 1: 0, 2: 0})
            for b in s.boxes:
                grouped[(b['box'], b['nominal'])][b['is_rejected']] = b['count']
            box_item = QTreeWidgetItem([
                '    🏦 Учёт по кассетам (chk/rej/exit)', '', '', '', '', '', '',
                f'{len(grouped)} шт.'
            ])
            for col in range(8):
                box_item.setForeground(col, QBrush(COLOR_LIGHT_BLUE))
            fe_item.addChild(box_item)
            for (bank, nom), counters in sorted(grouped.items()):
                QTreeWidgetItem(box_item, [
                    f'       └─ bank {bank} ${nom}', '', '', '', '', '', '',
                    f"CHK={counters[0]} REJ={counters[1]} EXIT={counters[2]}"
                ])

        if s.recovery_coupon:
            rc_item = QTreeWidgetItem([
                '    🎫 Возвратный купон', '', s.recovery_coupon, '', '',
                'чек напечатан' if s.receipt_printed else '', '', ''
            ])
            for col in range(8):
                rc_item.setForeground(col, QBrush(COLOR_DANGER))
            fe_item.addChild(rc_item)

        if s.lowlevel_sessions:
            self._add_w2_hw_block(sess_item, s)
        else:
            no_hw_item = QTreeWidgetItem([
                '  ⚙️ Железо: нет склейки с low-level логом',
                '', '', '', '', '', '',
                'нет devices_server-данных для этой операции'
            ])
            for col in range(8):
                no_hw_item.setForeground(col, QBrush(COLOR_MUTED))
            sess_item.addChild(no_hw_item)

        dur = s.duration_sec()
        if dur > 0:
            QTreeWidgetItem(sess_item, [
                f'  ⏱ Длительность операции', '', '', '', '', '', '',
                f'{dur:.1f} сек'
            ])

    def _add_w2_hw_block(self, parent, s):
        n = len(s.lowlevel_sessions)
        total_ll_disp = sum(ll.total_dispensed() for ll in s.lowlevel_sessions)
        total_ll_req = sum(ll.total_requested() for ll in s.lowlevel_sessions)
        models = sorted({ll.model for ll in s.lowlevel_sessions if ll.model})
        models_str = "/".join(models) if models else '?'
        has_susp = s.has_suspicious_match()
        susp_marker = " ⚠️" if has_susp else ""

        if n == 1:
            hw_text = (f'  ⚙️ Железо: реальная работа диспенсера '
                       f'({models_str}){susp_marker}')
        else:
            hw_text = (f'  ⚙️ Железо: реальная работа диспенсера '
                       f'({models_str}) — {n} LL-сессий{susp_marker}')
        hw_summary = f"выдано {total_ll_disp}/{total_ll_req} купюр"
        if has_susp:
            hw_summary += f", ⚠️ подозрительных: {s.suspicious_count()}"

        hw_item = QTreeWidgetItem([
            hw_text, '', '', '', '', '', '', hw_summary
        ])
        hw_color = COLOR_SUSPICIOUS if has_susp else COLOR_PURPLE
        for col in range(8):
            hw_item.setForeground(col, QBrush(hw_color))
        parent.addChild(hw_item)
        hw_item.setExpanded(True)

        if n == 1:
            ll = s.lowlevel_sessions[0]
            flags = s.suspicion_flags[0] if s.suspicion_flags else []
            self._add_ll_session_item(hw_item, ll, flags,
                                       prefix="     ▸ ", attempt_idx=None)
        else:
            for attempt_idx, ll in enumerate(s.lowlevel_sessions, 1):
                flags = (s.suspicion_flags[attempt_idx - 1]
                         if attempt_idx - 1 < len(s.suspicion_flags) else [])
                self._add_ll_session_item(
                    hw_item, ll, flags,
                    prefix=f"     ▸ LL-сессия #{attempt_idx} ",
                    attempt_idx=attempt_idx
                )

    def _add_ll_session_item(self, parent, ll, flags, prefix, attempt_idx=None):
        ll_status = W2_STATUS_DISPLAY.get(ll.status, ll.status)
        if ll.error_detail:
            ll_status = f"{ll_status} ({ll.error_detail})"
        reqid = ll.req_id or (f"idx:{ll.idx[:12]}…" if ll.idx else '?')
        susp_text = ("  ⚠️ " + ", ".join(flags)) if flags else ""
        banks = sorted({c.get('bankIndex') for c in ll.commands
                        if c.get('bankIndex') is not None})
        banks_str = f"bank {','.join(str(b) for b in banks)}" if banks else ""

        if attempt_idx is None:
            label = (f"{prefix}{ll.model} [{ll.start_time_str}] reqID={reqid} | "
                     f"{ll.total_dispensed()}/{ll.total_requested()} куп. | "
                     f"{ll_status}{susp_text}")
            summary = ""
        else:
            label = f"{prefix}[{ll.start_time_str}]{susp_text}"
            summary = (f"{ll.model} {banks_str} reqID={reqid} | "
                       f"{ll.total_dispensed()}/{ll.total_requested()} куп. | "
                       f"{ll_status}")

        if flags:
            color = COLOR_SUSPICIOUS
        elif ll.status.startswith('FAILED'):
            color = COLOR_DANGER
        else:
            color = COLOR_PURPLE

        ll_item = QTreeWidgetItem([label, '', '', '', '', '', '', summary])
        for col in range(8):
            ll_item.setForeground(col, QBrush(color))
        parent.addChild(ll_item)
        ll_item.setExpanded(ll.status != 'OK' or bool(flags))

        for ev in ll.events:
            self._add_ll_event_item(ll_item, ev, is_w2=True)

    def _add_ll_event_item(self, parent, ev, is_w2):
        kind = ev['kind']
        dt = ev.get('dt')
        time_s = dt.strftime("%H:%M:%S") if dt else '?'

        label, details, color = self._format_ll_event(kind, time_s, ev)

        cols = [label, '', '', '', '', '', '', details]
        item = QTreeWidgetItem(cols)
        for col in range(8):
            item.setForeground(col, QBrush(color))
        parent.addChild(item)

    def _format_ll_event(self, kind, time_s, ev):
        color = COLOR_PURPLE
        if kind == 'rawHex' or kind == 'writePayload':
            label = f"     └─ {time_s} 📨 hex"
            details = ev.get('hex') or ev.get('payload') or ''
            color = COLOR_LIGHT_BLUE
        elif kind == 'sensors':
            label = f"     └─ {time_s} 📡 sensors"
            details = (f"upper CHK={ev.get('upper_chk')} "
                       f"EXIT={ev.get('upper_exit')}; "
                       f"lower CHK={ev.get('lower_chk')} "
                       f"EXIT={ev.get('lower_exit')}")
        elif kind == 'statusCode':
            code = ev.get('status_code')
            text = ev.get('status_text', '')
            label = f"     └─ {time_s} 🔎 status"
            details = f"0x{code:02X} ({text})" if code is not None else text
            if code and code not in (0x30, 0x31):
                color = COLOR_DANGER
        elif kind == 'cmdOk':
            label = f"     └─ {time_s} ✅ CommandDispense"
            details = (f"chk={ev.get('chk')} disp={ev.get('dispensed')} "
                       f"rej={ev.get('rejected')}")
            total = ev.get('total_dispensed')
            if total is not None:
                details += f" (накоплено: {total})"
        elif kind == 'cmdErr':
            label = f"     └─ {time_s} ⛔ Dispense ERROR"
            details = (f"bank {ev.get('bankIndex')} chk={ev.get('chk')} "
                       f"disp={ev.get('dispensed')} rej={ev.get('rejected')} | "
                       f"{ev.get('error') or '?'}")
            color = COLOR_DANGER
        elif kind == 'noteResult':
            label = f"     └─ {time_s} 📄 note result"
            details = (f"bank {ev.get('bankIndex')} "
                       f"code={ev.get('device_code')} "
                       f"disp={ev.get('dispensed')} rej={ev.get('rejected')} | "
                       f"{ev.get('description') or ''}")
        elif kind == 'lcdm4000Err':
            label = f"     └─ {time_s} ⛔ LCDM4000 ERROR"
            details = f"code={ev.get('device_code')} | {ev.get('error') or '?'}"
            color = COLOR_DANGER
        elif kind == 'lcdm4000Stop':
            label = f"     └─ {time_s} 🛑 stop"
            details = f"bank {ev.get('bankIndex')} code={ev.get('device_code')}"
            color = COLOR_DANGER
        elif kind == 'lcdm4000Finished':
            label = f"     └─ {time_s} 🏁 finished"
            details = (f"bank {ev.get('bankIndex')} "
                       f"chk={ev.get('chk_sensor')} "
                       f"disp={ev.get('dispensed')} req={ev.get('requested')} "
                       f"rej={ev.get('rejected')}")
        elif kind == 'handleHeader':
            label = f"     └─ {time_s} 🚀 request start"
            details = f"commands={ev.get('commands_count')}"
        elif kind == 'handleCmd':
            label = f"     └─ {time_s} 📝 cmd"
            details = (f"idx={ev.get('cmdIndex')} bank {ev.get('bankIndex')} "
                       f"amount={ev.get('amount')}")
        elif kind == 'workerStart':
            label = f"     └─ {time_s} ⚙️ worker start"
            details = f"jobID={ev.get('jobID')}"
        elif kind == 'cmdStart':
            label = f"     └─ {time_s} ▶️ cmd start"
            details = (f"idx={ev.get('cmdIndex')} bank {ev.get('bankIndex')} "
                       f"amount={ev.get('amount')}")
        elif kind == 'seqStart':
            label = f"     └─ {time_s} ▶️ seq start"
            details = f"bank {ev.get('bankIndex')} amount={ev.get('amount')}"
        elif kind == 'settled':
            label = f"     └─ {time_s} ⚖️ settled"
            details = (f"bank {ev.get('bankIndex')} amount={ev.get('amount')} "
                       f"attempts={ev.get('attempts')}")
        elif kind == 'eventOk':
            label = f"     └─ {time_s} 🏁 event OK"
            details = (f"bank {ev.get('bankIndex')} "
                       f"requested={ev.get('requested')} "
                       f"chk={ev.get('chk')} disp={ev.get('dispensed')} "
                       f"rej={ev.get('rejected')}")
        elif kind == 'jobDone':
            label = f"     └─ {time_s} ✔️ job done"
            details = f"jobID={ev.get('jobID')}"
        else:
            label = f"     └─ {time_s} {kind}"
            details = str(ev.get('raw', ''))[:100]
        return label, details, color

    # ------------------------------------------------------------------
    # ВКЛАДКА «⚙️ W2 LOW-LEVEL»
    # ------------------------------------------------------------------

    def _build_w2ll_tab(self, data: dict) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        for text, status in [
            ("🔄 Все", None),
            ("✅ Успешные", 'OK'),
            ("⛔ Ошибки", 'FAILED_ANY'),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("compactButton")
            btn.clicked.connect(
                lambda _, s=status: self._refresh_w2ll(tab, data, filter_status=s)
            )
            filter_row.addWidget(btn)
        filter_row.addStretch()

        filter_row.addWidget(QLabel("Модель:"))
        model_combo = QComboBox()
        model_combo.addItem("Все")
        model_combo.setMinimumWidth(200)
        w2_ll: W2LowLevelAnalyzer = data['w2_lowlevel']
        models = sorted({s.model for s in w2_ll.sessions if s.model})
        for m in models:
            model_combo.addItem(m)
        model_combo.currentIndexChanged.connect(
            lambda: self._refresh_w2ll(tab, data)
        )
        tab.model_combo = model_combo
        filter_row.addWidget(model_combo)

        tab.info_label = QLabel("Сессий: 0")
        tab.info_label.setStyleSheet("color: #9ca3bf;")
        filter_row.addWidget(tab.info_label)
        layout.addLayout(filter_row)

        tree = QTreeWidget()
        tree.setColumnCount(8)
        tree.setHeaderLabels([
            "Дата / Сессия", "Время", "reqID / idx", "Модель",
            "Запр.", "Выд.", "Статус", "Детали"
        ])
        self._setup_tree_header(tree, [320, 85, 140, 120, 80, 80, 220])
        layout.addWidget(tree, stretch=1)
        tab.tree = tree

        self._refresh_w2ll(tab, data)
        return tab

    def _refresh_w2ll(self, tab, data, filter_status=None):
        tree: QTreeWidget = tab.tree
        tree.clear()
        w2_ll: W2LowLevelAnalyzer = data['w2_lowlevel']
        sessions = list(w2_ll.sessions)
        if filter_status:
            if filter_status == 'FAILED_ANY':
                sessions = [s for s in sessions if s.status.startswith('FAILED')]
            else:
                sessions = [s for s in sessions if s.status == filter_status]
        model_filter = tab.model_combo.currentText()
        if model_filter and model_filter != 'Все':
            sessions = [s for s in sessions if s.model == model_filter]
        tab.info_label.setText(f"Low-Level сессий: {len(sessions)}")
        if not sessions:
            return

        by_date = defaultdict(list)
        for s in sessions:
            by_date[s.start_date_str or "Неизвестно"].append(s)

        for date in sorted(by_date.keys()):
            sess_list = by_date[date]
            ok_cnt = sum(1 for s in sess_list if s.status == 'OK')
            date_item = QTreeWidgetItem([
                f'📅 {date}', '', '', '', '', '',
                f'{ok_cnt}/{len(sess_list)} ok', ''
            ])
            self._apply_date_style(date_item)
            tree.addTopLevelItem(date_item)

            for i, s in enumerate(sorted(sess_list,
                                          key=lambda x: x.start_dt or datetime.min),
                                  1):
                status_txt = W2_STATUS_DISPLAY.get(s.status, s.status)
                if s.error_detail and s.status.startswith('FAILED'):
                    status_txt = f"{status_txt} ({s.error_detail})"
                if s.status == 'OK':
                    color = COLOR_SUCCESS
                elif s.status.startswith('FAILED'):
                    color = COLOR_DANGER
                elif s.status == 'PARTIAL':
                    color = COLOR_WARNING
                else:
                    color = COLOR_MUTED

                reqid_display = str(s.req_id) if s.req_id else (
                    f"idx:{s.idx[:12]}…" if s.idx else '?'
                )
                details_parts = []
                if s.commands:
                    details_parts.append(f"{len(s.commands)} команд")
                if s.lcdm_status_code is not None:
                    details_parts.append(f"0x{s.lcdm_status_code:02X}")
                if s.lcdm_device_code is not None:
                    details_parts.append(f"code={s.lcdm_device_code}")
                details_str = (", ".join(details_parts) if details_parts
                               else f"{len(s.events)} событий")

                sess_item = QTreeWidgetItem([
                    f'#{i}', s.start_time_str, reqid_display, s.model or '?',
                    str(s.total_requested()), str(s.total_dispensed()),
                    status_txt, details_str
                ])
                for col in range(8):
                    sess_item.setForeground(col, QBrush(color))
                date_item.addChild(sess_item)
                sess_item.setExpanded(s.status != 'OK')

                if s.commands:
                    cmd_item = QTreeWidgetItem([
                        '  📝 Команды', '', '', '', '', '', '',
                        f'{len(s.commands)} шт.'
                    ])
                    sess_item.addChild(cmd_item)
                    for ci, cmd in enumerate(s.commands, 1):
                        QTreeWidgetItem(cmd_item, [
                            f'     └─ #{ci}', '', '', '', '', '', '',
                            f"cmdIndex={cmd.get('cmdIndex')} "
                            f"bank {cmd.get('bankIndex')} "
                            f"amount={cmd.get('amount')}"
                        ])
                if s.results:
                    res_item = QTreeWidgetItem([
                        '  📊 Результаты', '', '', '', '', '', '',
                        f'{len(s.results)} шт.'
                    ])
                    sess_item.addChild(res_item)
                    for ri, r in enumerate(s.results, 1):
                        has_err = (bool(r.get('error')) or
                                   (r.get('status_code') not in
                                    (None, 0x30, 0x31)))
                        parts = []
                        if 'chk_sensor' in r:
                            parts.append(f"chk={r['chk_sensor']}")
                        if 'dispensed' in r:
                            parts.append(f"disp={r['dispensed']}")
                        if 'rejected' in r:
                            parts.append(f"rej={r['rejected']}")
                        if r.get('status_code') is not None:
                            parts.append(f"0x{r['status_code']:02X}")
                        if r.get('status_text'):
                            parts.append(r['status_text'])
                        if r.get('error'):
                            parts.append(f"ERR: {r['error']}")
                        r_item = QTreeWidgetItem([
                            f'     └─ #{ri}', '', '', '', '', '', '',
                            ", ".join(parts)
                        ])
                        if has_err:
                            for col in range(8):
                                r_item.setForeground(col, QBrush(COLOR_DANGER))
                        res_item.addChild(r_item)
                for ev in s.events:
                    self._add_ll_event_item(sess_item, ev, is_w2=False)
                dur = s.duration_sec()
                if dur > 0:
                    QTreeWidgetItem(sess_item, [
                        f'  ⏱ Длительность', '', '', '', '', '', '',
                        f'{dur:.1f} сек'
                    ])

    # ------------------------------------------------------------------
    # ВКЛАДКА «📡 HEALTH»
    # ------------------------------------------------------------------

    def _build_health_tab(self, data: dict) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        filter_row.addWidget(QLabel("Режим:"))
        tab.health_mode = 'changes'
        changes_rb = QRadioButton("Только изменения")
        changes_rb.setChecked(True)
        changes_rb.toggled.connect(
            lambda checked: self._on_health_mode_changed(
                tab, data, 'changes' if checked else None
            )
        )
        filter_row.addWidget(changes_rb)
        all_rb = QRadioButton("Все записи")
        all_rb.toggled.connect(
            lambda checked: self._on_health_mode_changed(
                tab, data, 'all' if checked else None
            )
        )
        filter_row.addWidget(all_rb)
        filter_row.addStretch()
        tab.info_label = QLabel("Записей: 0")
        tab.info_label.setStyleSheet("color: #9ca3bf;")
        filter_row.addWidget(tab.info_label)
        layout.addLayout(filter_row)

        tree = QTreeWidget()
        tree.setColumnCount(5)
        tree.setHeaderLabels([
            "Дата", "Время", "Тип", "Объект", "Детали"
        ])
        self._setup_tree_header(tree, [220, 85, 140, 160])
        layout.addWidget(tree, stretch=1)
        tab.tree = tree

        self._refresh_health(tab, data)
        return tab

    def _on_health_mode_changed(self, tab, data, mode):
        if mode is None:
            return
        tab.health_mode = mode
        self._refresh_health(tab, data)

    def _refresh_health(self, tab, data):
        tree: QTreeWidget = tab.tree
        tree.clear()
        w2_ll: W2LowLevelAnalyzer = data['w2_lowlevel']
        records = (w2_ll.lcdm_health_changes if tab.health_mode == 'changes'
                   else w2_ll.lcdm_health)
        mode_label = ("только изменения" if tab.health_mode == 'changes'
                      else "все записи")
        tab.info_label.setText(f"Записей: {len(records)} ({mode_label})")
        if not records:
            return

        by_date = defaultdict(list)
        for kind, state in records:
            dt = state.get('dt')
            date_str = dt.strftime("%d/%m/%Y") if dt else "Неизвестно"
            by_date[date_str].append((kind, state))

        for date in sorted(by_date.keys()):
            day_recs = by_date[date]
            date_item = QTreeWidgetItem([
                f'📅 {date}', '', '', '', f'{len(day_recs)} записей'
            ])
            self._apply_date_style(date_item)
            tree.addTopLevelItem(date_item)

            for kind, state in sorted(day_recs,
                                       key=lambda x: x[1].get('dt') or
                                                      datetime.min):
                dt = state.get('dt')
                time_s = dt.strftime("%H:%M:%S") if dt else '?'
                if kind == 'sensors':
                    details = (f"DVTL={state['DVTL']} DVTR={state['DVTR']} "
                               f"EJT={state['EJT']} EXIT={state['EXIT']} "
                               f"RJT={state['RJT']}")
                    all_ok = all(state[k] == 'OK' for k in
                                 ('DVTL', 'DVTR', 'EJT', 'EXIT', 'RJT'))
                    color = COLOR_SUCCESS if all_ok else COLOR_DANGER
                    rec_item = QTreeWidgetItem([
                        f'  └─ {time_s}', time_s, '📡 sensors',
                        'LCDM4000', details
                    ])
                else:
                    num = state['num']
                    details = (f"exists={state['exists']} "
                               f"length={state['length']} "
                               f"opacity={state['opacity']} "
                               f"status={state['status']}")
                    color = (COLOR_DANGER if state['exists'] != 'YES'
                             else COLOR_LIGHT_BLUE)
                    rec_item = QTreeWidgetItem([
                        f'  └─ {time_s}', time_s, '🏦 cassette',
                        f'Cassette {num}', details
                    ])
                for col in range(5):
                    rec_item.setForeground(col, QBrush(color))
                date_item.addChild(rec_item)

    # ------------------------------------------------------------------
    # УТИЛИТЫ
    # ------------------------------------------------------------------

    def _apply_date_style(self, item: QTreeWidgetItem):
        """Жирный шрифт + фон для строки даты."""
        font = QFont()
        font.setBold(True)
        for col in range(item.columnCount()):
            item.setFont(col, font)
            item.setBackground(col, QBrush(COLOR_DATE_BG))

    def _setup_tree_header(self, tree: QTreeWidget, section_widths: list):
        """Настраивает header таблицы:
        - Первая колонка (дерево) — Stretch.
        - Средние — Interactive с заданной начальной шириной.
        - Последняя — Stretch.
        - Применяет компактный шрифт 8pt ко всему дереву.
        """
        # Компактный шрифт 8pt для максимальной плотности данных
        tree_font = QFont()
        tree_font.setPointSize(8)
        tree.setFont(tree_font)

        # Шрифт для заголовка таблицы — чуть крупнее, чем контент (9pt)
        header_font = QFont()
        header_font.setPointSize(9)
        header_font.setBold(True)
        tree.header().setFont(header_font)

        # Уменьшаем высоту строк (по умолчанию ~22px, станет ~16px)
        existing_style = tree.styleSheet() or ""
        tree.setStyleSheet(existing_style + """
            QTreeWidget::item {
                padding: 1px 2px;
            }
        """)

        header = tree.header()
        header.setStretchLastSection(False)
        col_count = tree.columnCount()

        for i in range(col_count):
            if i == 0:
                header.setSectionResizeMode(i, QHeaderView.Stretch)
            elif i == col_count - 1:
                header.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(i, QHeaderView.Interactive)
                if i < len(section_widths):
                    header.resizeSection(i, section_widths[i])

    # ------------------------------------------------------------------
    # Сохранение .anl.gz
    # ------------------------------------------------------------------

    def _on_save_analysis(self):
        if not self.devices_data:
            QMessageBox.warning(self, "Нет данных", "Нет данных для сохранения.")
            return

        if len(self.devices_data) == 1:
            device_id = next(iter(self.devices_data.keys()))
            data = self.devices_data[device_id]
            self._save_one_device(data, device_id)
        else:
            reply = QMessageBox.question(
                self, "Сохранение анализа",
                f"Устройств: {len(self.devices_data)}.\n\n"
                f"Yes — сохранить все (отдельные файлы).\n"
                f"No — только текущее устройство.\nCancel — отменить.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if reply == QMessageBox.Cancel:
                return
            if reply == QMessageBox.Yes:
                self._save_all_devices()
            else:
                if hasattr(self, 'device_tabs'):
                    idx = self.device_tabs.currentIndex()
                    device_ids = sorted(self.devices_data.keys())
                    if 0 <= idx < len(device_ids):
                        did = device_ids[idx]
                        self._save_one_device(self.devices_data[did], did)

    def _save_one_device(self, data, device_id):
        default_name = (f"analysis_{device_id}_"
                        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.anl.gz")
        fp, _ = QFileDialog.getSaveFileName(
            self, "Сохранить анализ", default_name,
            "Analysis files (*.anl.gz);;All files (*)"
        )
        if not fp:
            return
        try:
            AnalysisSerializer.save(
                fp, data['lima'], data['w2_frontend'],
                data['w2_lowlevel'], data.get('files', [])
            )
            size_mb = os.path.getsize(fp) / 1024 / 1024
            QMessageBox.information(
                self, "Сохранено",
                f"Анализ сохранён:\n{fp}\n\nРазмер: {size_mb:.2f} МБ"
            )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения",
                                 traceback.format_exc())

    def _save_all_devices(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Выберите папку для сохранения"
        )
        if not folder:
            return
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        saved, errors = 0, []
        for device_id, data in self.devices_data.items():
            fp = Path(folder) / f"analysis_{device_id}_{timestamp}.anl.gz"
            try:
                AnalysisSerializer.save(
                    str(fp), data['lima'], data['w2_frontend'],
                    data['w2_lowlevel'], data.get('files', [])
                )
                saved += 1
            except Exception as e:
                errors.append(f"{device_id}: {e}")
        msg = f"Сохранено: {saved} файлов"
        if errors:
            msg += f"\n\nОшибок: {len(errors)}\n" + "\n".join(errors[:5])
        QMessageBox.information(self, "Сохранено", msg)

    def closeEvent(self, event: QCloseEvent):
        """Данные (devices_data) остаются в ParserPage, окно можно открыть снова."""
        self.window_closed.emit(self)
        super().closeEvent(event)