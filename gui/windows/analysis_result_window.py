"""Отдельное окно с результатами анализа.

Открывается при каждом запуске нового анализа или загрузке .anl.gz.
Содержит вкладки по устройствам (если их несколько), в каждой — только
те внутренние вкладки анализа, в которых реально есть данные.
"""

import os
import re
import traceback
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QFont, QBrush, QColor, QCloseEvent,
                           QGuiApplication, QPen)
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStyledItemDelegate, QStyleOptionViewItem,
    QComboBox, QTabWidget, QTextEdit, QFileDialog, QMessageBox,
    QFrame, QTreeWidget, QTreeWidgetItem, QHeaderView, QRadioButton,
    QLineEdit, QScrollArea, QSplitter, QScrollBar, QMenu,
    QAbstractItemView,
)

from version import APP_NAME
from core.constants import (
    ALL_DENOMINATIONS, DENOM_MAP, BNV_EVENT_NAMES, W2_STATUS_DISPLAY,
)
from parsers.lima_parser import LogAnalyzer
from parsers.w2_frontend_parser import W2FrontendAnalyzer
from parsers.w2_lowlevel_parser import W2LowLevelAnalyzer
from parsers.serializer import AnalysisSerializer


class _CellHighlightDelegate(QStyledItemDelegate):
    """Подсвечивает одну ячейку дерева.

    Обычный setBackground() игнорируется, когда у вида задан stylesheet,
    поэтому фон рисуем сами в делегате.
    """

    def __init__(self, state: dict, color: str, tree, parent=None):
        super().__init__(parent)
        self._state = state
        self._color = QColor(color)
        self._tree = tree

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        super().paint(painter, opt, index)

        cell = self._state.get("cell")
        if cell is None:
            return
        item, column = cell
        try:
            is_target = index == self._tree.indexFromItem(item, column)
        except RuntimeError:
            is_target = False
        if not is_target:
            return

        # Рисуем поверх ячейки: стили Qt иначе перекрывают фон.
        fill = QColor("#6366f1")
        fill.setAlpha(120)
        painter.save()
        painter.fillRect(option.rect, fill)
        pen = QPen(QColor("#c7d2fe"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
        painter.restore()


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
BG_WINDOW_COLOR = "#1a1b2e"

# Пресеты оформления окна результатов. Выбираются переменной окружения
# FLUXUS_RESULT_THEME (dark | soft | light), по умолчанию — soft.
RESULT_THEMES = {
    "dark": {
        "win_top": "#1e2036", "win_bottom": "#161726",
        "panel": "#24253c", "panel_alt": "#292a44", "header": "#2a2b47",
        "detail": "#2a2b47", "border": "#3a3b58", "hover": "#313252",
        "select": "#3d3f68", "text": "#e4e5f1",
    },
    "soft": {
        "win_top": "#282a48", "win_bottom": "#1c1d32",
        "panel": "#2b2c48", "panel_alt": "#323353", "header": "#35365a",
        "detail": "#33345a", "border": "#43456b", "hover": "#3b3d66",
        "select": "#4a4c7d", "text": "#eceefa",
    },
    "light": {
        "win_top": "#33355a", "win_bottom": "#252642",
        "panel": "#363858", "panel_alt": "#3d3f63", "header": "#41436c",
        "detail": "#3f4168", "border": "#4e5080", "hover": "#474a78",
        "select": "#575a92", "text": "#f2f3ff",
    },
}


def norm_coupon(value) -> str:
    """Номер купона без пробелов, дефисов и прочих символов."""
    return re.sub(r"\D", "", str(value or ""))


def _result_theme():
    import os as _os
    name = _os.environ.get("FLUXUS_RESULT_THEME", "soft").lower()
    return RESULT_THEMES.get(name, RESULT_THEMES["soft"])


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

    # Подписи для событий устройства (инкассация / изъятие / пополнение)
    CASH_TYPE_DISPLAY = {
        "collection": "📦 Инкассация",
        "withdraw_report": "💸 Изъятие",
        "cash_in": "📥 Пополнение",
        "refill": "📥 Пополнение кассет",
    }

    """Отдельное окно с результатами анализа.

    Структура:
      - Если устройств несколько — верхние вкладки по устройствам.
      - Внутри каждой — только те внутренние вкладки, где есть данные.
      - Внизу — кнопки сохранения и закрытия окна.

    Размер окна: 90% от доступной геометрии экрана, центрировано.
    """

    window_closed = Signal(object)
    visibility_changed = Signal(bool)

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
        t = _result_theme()
        self.setStyleSheet(f"""
            QMainWindow {{
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 {t['win_top']},
                                            stop:1 {t['win_bottom']});
            }}
            QWidget#centralWidget {{ background: transparent; }}
            QTreeWidget {{
                background: {t['panel']};
                border: 1px solid {t['border']};
                border-radius: 10px;
                alternate-background-color: {t['panel_alt']};
                outline: none;
                color: {t['text']};
            }}
            QTreeWidget::item {{ padding: 5px 6px; border-bottom: 1px solid {t['border']}; }}
            QTreeWidget::item:hover {{ background: {t['hover']}; }}
            QTreeWidget::item:selected {{
                background: {t['select']}; color: #ffffff;
                border-left: 3px solid #6366f1;
            }}
            QTreeWidget::item:selected:!active {{
                background: {t['select']}; color: #ffffff;
            }}
            QHeaderView::section {{
                background: {t['header']};
                color: #cdd1e8;
                border: none;
                border-bottom: 1px solid {t['border']};
                padding: 7px 6px;
                font-weight: 600;
            }}
            QSplitter::handle {{ background: {t['border']}; }}
            QSplitter::handle:hover {{ background: #6366f1; }}
        """)

        central = QWidget()
        central.setObjectName("centralWidget")
        central.setStyleSheet(
            "QWidget#centralWidget { background: transparent; }"
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
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        kpis = self._stats_kpis(data)
        sections = self._stats_sections(data)
        tab._all_text = self._stats_plain_text(data, kpis, sections)

        top = QHBoxLayout()
        title = QLabel("Сводка по анализу")
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        top.addWidget(title)
        top.addStretch()
        copy_all = QPushButton("  Копировать весь отчёт")
        copy_all.setObjectName("compactButton")
        copy_all.setCursor(Qt.PointingHandCursor)
        copy_all.clicked.connect(lambda: self._copy_to_clipboard(tab._all_text))
        top.addWidget(copy_all)
        outer.addLayout(top)

        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(8)
        for label, value, sub, color in kpis:
            card = QFrame()
            card.setObjectName("kpiCard")
            card.setStyleSheet(
                "QFrame#kpiCard { background: #232438; border: 1px solid #34354e;"
                " border-radius: 10px; }"
            )
            lay = QVBoxLayout(card)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(2)
            k = QLabel(label.upper())
            k.setStyleSheet("color: #9ca3bf; font-size: 10px; letter-spacing: 0.5px;")
            v = QLabel(value)
            v.setStyleSheet(f"color: {color}; font-size: 19px; font-weight: 700;")
            sm = QLabel(sub)
            sm.setStyleSheet("color: #9ca3bf; font-size: 10px;")
            sm.setWordWrap(True)
            lay.addWidget(k)
            lay.addWidget(v)
            lay.addWidget(sm)
            kpi_row.addWidget(card, 1)
        outer.addLayout(kpi_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        box = QVBoxLayout(container)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        for section_title, text in sections:
            box.addWidget(self._make_stats_section(section_title, text))
        box.addStretch(1)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)
        return tab

    def _make_stats_section(self, title: str, text: str) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background: #232438; border: 1px solid #34354e;"
            " border-radius: 8px; }"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(12, 8, 8, 8)
        hl.setSpacing(8)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600; font-size: 12px;")
        hl.addWidget(t)
        hl.addStretch()

        lines = max(1, text.count("\n") + 1)
        height = min(280, 24 + 18 * lines)

        body = QTextEdit()
        body.setObjectName("statsBody")
        body.setReadOnly(True)
        body.setFont(QFont("Consolas", 10))
        body.setPlainText(text)
        # Высоту задаём и в стилях: глобальная тема содержит
        # "QTextEdit { min-height: 24px }" и иначе сжимает блок до одной строки.
        body.setStyleSheet(
            "QTextEdit#statsBody { background: #2a2b40; color: #e4e5f1;"
            " border: none; border-top: 1px solid #34354e; padding: 8px;"
            f" min-height: {height}px; max-height: {height}px; }}"
        )
        body.setFixedHeight(height)

        btn = QPushButton("Копировать")
        btn.setObjectName("compactButton")
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._copy_to_clipboard(body.toPlainText()))
        hl.addWidget(btn)
        lay.addWidget(head)
        lay.addWidget(body)
        return frame

    def _stats_kpis(self, data: dict) -> list:
        fe = data.get('w2_frontend')
        sessions = list(getattr(fe, "sessions", []) or [])
        cash = list(getattr(fe, "cash_events", []) or [])

        # Для Lima-терминала (без W2) показываем статистику купюр
        lima = data.get('lima')
        if not sessions and not cash and getattr(lima, "sessions", None):
            lima_sessions = list(lima.sessions)
            accepted = sum(1 for s in lima_sessions if s.status == 'STACKED')
            rejected = sum(1 for s in lima_sessions
                           if s.status in ('REJECTED', 'ERROR'))
            total = sum(sum(d.values()) for d in (lima.stats or {}).values())
            amount = sum(
                sum(float(k) * v for k, v in denoms.items())
                for denoms in (lima.stats or {}).values()
            )
            return [
                ("Принято купюр", str(total),
                 f"{accepted} сессий принято · {rejected} отклонено", "#e4e5f1"),
                ("Сумма", f"${amount:,.0f}",
                 f"сессий: {len(lima_sessions)}", "#10b981"),
                ("Инкассации", str(len(getattr(lima, "collections", []) or [])),
                 "операций сдачи", "#6366f1"),
                ("Отклонено", str(rejected),
                 "REJECTED + ERROR", "#ef4444" if rejected else "#10b981"),
            ]

        total = len(sessions)
        ok = sum(1 for s in sessions if s.status == 'OK')
        failed = sum(1 for s in sessions if s.status.startswith('FAILED'))
        req = sum(s.requested_amount for s in sessions) / 100
        disp = sum(s.dispensed_amount for s in sessions) / 100
        pct = (disp / req * 100) if req else 0.0

        out_sum = sum(getattr(e, "total_cents", 0) for e in cash) / 100
        in_sum = sum(getattr(e, "total_loaded_cents", 0) for e in cash) / 100

        reasons = defaultdict(int)
        for s in sessions:
            if s.error_detail:
                reasons[s.error_detail] += 1
        top_reason = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else "нет"

        return [
            ("Выдач", str(total), f"{ok} успешных · {failed} с ошибкой", "#e4e5f1"),
            ("Запрошено / выдано", f"${req:,.0f} / ${disp:,.0f}",
             f"выдано {pct:.1f} %", "#10b981" if pct >= 95 else "#f59e0b"),
            ("Инкассации и изъятия", str(len(cash)),
             f"OUT ${out_sum:,.2f} · IN ${in_sum:,.2f}", "#6366f1"),
            ("Ошибки", str(failed), f"чаще всего: {top_reason}",
             "#ef4444" if failed else "#10b981"),
        ]

    def _stats_sections(self, data: dict) -> list:
        fe = data.get('w2_frontend')
        ll = data.get('w2_lowlevel')
        lima = data.get('lima')
        label = data.get('label', 'устройство')
        sessions = list(getattr(fe, "sessions", []) or [])
        cash = list(getattr(fe, "cash_events", []) or [])

        sections = []

        req = sum(s.requested_amount for s in sessions) / 100
        disp = sum(s.dispensed_amount for s in sessions) / 100
        suspicious = sum(1 for s in sessions if s.has_suspicious_match())

        lima_sessions = list(getattr(lima, "sessions", []) or []) if lima else []
        if not sessions and not cash and lima_sessions:
            # Сводка для Lima-терминала: купюры, а не выдачи
            lima_stats = getattr(lima, "stats", {}) or {}
            total_count = sum(sum(d.values()) for d in lima_stats.values())
            amount = sum(
                sum(float(k) * v for k, v in denoms.items())
                for denoms in lima_stats.values()
            )
            rejected = sum(1 for x in lima_sessions
                           if x.status in ('REJECTED', 'ERROR'))
            lines = [
                f"Устройство:          {label}",
                f"Сессий купюр:        {len(lima_sessions)}",
                f"Принято купюр:       {total_count}",
                f"Сумма:               ${amount:,.2f}",
                f"Отклонено/ошибки:    {rejected}",
                f"Инкассаций:          {len(getattr(lima, 'collections', []) or [])}",
            ]
        else:
            lines = [
                f"Устройство:          {label}",
                f"Выдач:               {len(sessions)}",
                f"Запрошено:           ${req:,.2f}",
                f"Выдано:              ${disp:,.2f}"
                + (f"  ({disp / req * 100:.1f} %)" if req else ""),
                f"Событий устройства:  {len(cash)}",
                f"Подозрительных:      {suspicious}",
            ]
            if ll and getattr(ll, 'sessions', None):
                lines.append(f"LL-сессий:           {len(ll.sessions)}")
            if lima_sessions:
                lines.append(f"Lima-сессий:         {len(lima_sessions)}")
        sections.append(("📋 Сводка", "\n".join(lines)))

        if cash:
            lines = []
            for e in sorted(cash, key=lambda x: x.dt or datetime.min):
                dt = e.dt.strftime("%d/%m/%Y %H:%M:%S") if e.dt else "?"
                kind = self.CASH_TYPE_DISPLAY.get(e.event_type, e.event_type)
                out_s = (f"OUT ${e.total_cents / 100:,.2f}"
                         if getattr(e, "total_cents", 0) else "")
                in_s = (f"IN ${e.total_loaded_cents / 100:,.2f}"
                        if getattr(e, "total_loaded_cents", 0) else "")
                amounts = "  ".join(x for x in (out_s, in_s) if x)
                lines.append(f"{dt}  {kind}  {amounts}")
                detail = self._cash_event_details(e)
                if detail:
                    lines.append(f"    {detail}")
            sections.append(("💰 Инкассации и изъятия", "\n".join(lines)))

        if sessions:
            counts = defaultdict(int)
            for s in sessions:
                counts[s.status] += 1
            lines = []
            for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
                name = W2_STATUS_DISPLAY.get(status, status)
                pct = count / len(sessions) * 100
                lines.append(f"{name:<24} {count:>5}   {pct:>5.1f} %")
            sections.append(("📈 Выдачи по статусам", "\n".join(lines)))

            reasons = defaultdict(int)
            for s in sessions:
                if s.error_detail:
                    reasons[s.error_detail] += 1
            if reasons:
                lines = [f"{reason:<40} {count:>4}"
                         for reason, count in
                         sorted(reasons.items(), key=lambda kv: -kv[1])[:12]]
                sections.append(("⚠️ Причины ошибок", "\n".join(lines)))

        lines = []
        if fe:
            for dev, info in sorted((getattr(fe, 'dispensers', {}) or {}).items()):
                model = info.get('model', '?') if isinstance(info, dict) else '?'
                vendor = info.get('vendor', '?') if isinstance(info, dict) else '?'
                lines.append(f"Диспенсер {model} ({vendor}) · {dev}")
        if ll:
            models = defaultdict(int)
            for sess in (getattr(ll, 'sessions', []) or []):
                models[sess.model or '?'] += 1
            for model, count in sorted(models.items()):
                lines.append(f"LL-сессий {model}: {count}")
            lines.append(f"Записей health:      {len(getattr(ll, 'lcdm_health', []) or [])}")
        if lima:
            for serial, info in sorted((getattr(lima, 'device_models', {}) or {}).items()):
                lines.append(f"Купюроприёмник {info.get('model', '?')} · SN {serial}")
        if lines:
            sections.append(("🖥️ Оборудование", "\n".join(lines)))

        if lima and (getattr(lima, 'sessions', None) or
                     getattr(lima, 'collections', None)):
            stats = lima.stats or {}
            total_count = sum(sum(d.values()) for d in stats.values())
            lines = [
                f"Сессий купюр:        {len(lima.sessions)}",
                f"Принято купюр:       {total_count}",
                f"Инкассаций:          {len(getattr(lima, 'collections', []) or [])}",
            ]
            # принято по номиналам
            for curr, denoms in sorted(stats.items()):
                notes = self._notes_text(denoms)
                if notes:
                    lines.append(f"Принято {curr}:        {notes}")
            # отклонённые / ошибки
            by_status = defaultdict(int)
            for sess in lima.sessions:
                by_status[sess.status] += 1
            parts = [f"{k}: {v}" for k, v in sorted(by_status.items()) if v]
            if parts:
                lines.append("Статусы сессий:      " + ", ".join(parts))
            # между инкассациями
            try:
                (between, b_count, b_sum, b_first, b_last) = \
                    lima.get_between_collections_stats()
            except Exception:
                between = None
            if between:
                lines.append("")
                lines.append("Между инкассациями:")
                lines.append(f"  принято купюр:     {b_count}")
                lines.append(f"  сумма:             ${b_sum:,.2f}")
                for curr, denoms in sorted(between.items()):
                    notes = self._notes_text(denoms)
                    if notes:
                        lines.append(f"  {curr}:             {notes}")
            if getattr(lima, 'bnv_events_count', None):
                top = sorted(lima.bnv_events_count.items(), key=lambda kv: -kv[1])[:6]
                lines.append("События BNV:         "
                             + ", ".join(f"{k} {v}" for k, v in top))
            sections.append(("💵 Купюроприёмник (Lima)", "\n".join(lines)))

        return sections

    @staticmethod
    def _stats_plain_text(data: dict, kpis: list, sections: list) -> str:
        label = data.get('label', 'устройство')
        lines = [f"СТАТИСТИКА — {label}", "=" * 60, ""]
        for title, value, sub, _color in kpis:
            lines.append(f"{title}: {value}  ({sub})")
        lines.append("")
        for title, text in sections:
            lines.append(title)
            lines.append("-" * 60)
            lines.append(text)
            lines.append("")
        return "\n".join(lines)

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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

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
            btn.clicked.connect(lambda _, st=status: self._set_lc_filter(tab, st))
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
        model_combo.currentIndexChanged.connect(lambda: self._refresh_lifecycle(tab))
        tab.model_combo = model_combo
        filter_row.addWidget(model_combo)
        layout.addLayout(filter_row)

        t = _result_theme()
        content = QHBoxLayout()
        content.setSpacing(10)

        left_panel = QFrame()
        left_panel.setObjectName("lcListPanel")
        left_panel.setStyleSheet(
            f"QFrame#lcListPanel {{ background: {t['panel']};"
            f" border: 1px solid {t['border']}; border-radius: 10px; }}"
        )
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(6, 6, 6, 6)

        tree = QTreeWidget()
        tree.setColumnCount(4)
        tree.setHeaderLabels(["Дата / Время", "Номинал", "Статус", "Модель"])
        tree.setRootIsDecorated(False)
        tree.setIndentation(0)
        tree.setUniformRowHeights(True)
        tree.setFrameShape(QFrame.NoFrame)
        tree.setStyleSheet(
            "QTreeWidget { background: transparent; border: none;"
            f" color: {t['text']}; }}"
            "QTreeWidget::item { padding: 5px 6px; }"
            f"QTreeWidget::item:selected {{ background: {t['select']};"
            " color: #ffffff; }"
        )
        tree.itemSelectionChanged.connect(lambda: self._on_lc_select(tab))
        self._install_copy_menu(tree)
        self._setup_tree_header(tree, [150, 90, 120, 140])
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addWidget(tree, 1)
        row.addSpacing(6)
        row.addWidget(self._attach_external_scrollbar(tree))
        left_layout.addLayout(row)
        content.addWidget(left_panel, 46)

        right_panel = QFrame()
        right_panel.setObjectName("lcDetailPanel")
        right_panel.setStyleSheet(
            f"QFrame#lcDetailPanel {{ background: {t['detail']};"
            f" border: 1px solid {t['border']}; border-radius: 10px; }}"
        )
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)

        detail = QTextEdit()
        detail.setObjectName("lcDetail")
        detail.setReadOnly(True)
        detail.setFont(QFont("Consolas", 10))
        detail.setFrameShape(QFrame.NoFrame)
        detail.setStyleSheet(
            f"QTextEdit#lcDetail {{ background: transparent; color: {t['text']};"
            " border: none; padding: 4px; }"
        )
        detail.setPlainText("Выберите купюру слева.")
        right_layout.addWidget(detail)
        tab.detail = detail
        content.addWidget(right_panel, 50)

        layout.addLayout(content, 1)
        tab.tree = tree
        tab._data = data
        tab._payloads = {}
        tab.current_filter = None
        self._refresh_lifecycle(tab, data)
        return tab

    def _set_lc_filter(self, tab, status):
        tab.current_filter = status
        self._refresh_lifecycle(tab)

    def _refresh_lifecycle(self, tab, data=None):
        data = data if data is not None else getattr(tab, "_data", None)
        if not data:
            return
        tree: QTreeWidget = tab.tree
        tree.clear()
        tab._payloads = {}
        analyzer: LogAnalyzer = data['lima']
        sessions = list(analyzer.sessions)

        incomplete = {'INCOMPLETE', 'INSERTED', 'STACKING'}
        status = getattr(tab, "current_filter", None)
        if status:
            if status == 'INCOMPLETE':
                sessions = [s for s in sessions if s.status in incomplete]
            else:
                sessions = [s for s in sessions if s.status == status]

        model_filter = tab.model_combo.currentText()
        if model_filter and model_filter != 'Все':
            sessions = [s for s in sessions if s.device_model == model_filter]

        by_date = defaultdict(list)
        for s in sessions:
            by_date[s.first_date or "Неизвестно"].append(s)

        def date_key(d):
            try:
                return datetime.strptime(d, "%d/%m/%Y")
            except (TypeError, ValueError):
                return datetime.max

        for date in sorted(by_date.keys(), key=date_key):
            sess_list = sorted(by_date[date], key=lambda x: x.start_ts)
            stacked = sum(1 for s in sess_list if s.status == 'STACKED')
            amount = sum(s.get_display_denom() for s in sess_list
                         if s.status == 'STACKED')
            date_item = QTreeWidgetItem([
                f'📅 {date}', f'{stacked}/{len(sess_list)}', f'${amount:.0f}', ''
            ])
            self._apply_date_style(date_item)
            tree.addTopLevelItem(date_item)

            for s in sess_list:
                denom = s.get_display_denom()
                curr = s.currency or 'USD'
                denom_txt = f"${denom:.0f}" if denom else '—'
                if s.status == 'STACKED':
                    status_txt, color = "✅ ПРИНЯТА", COLOR_SUCCESS
                elif s.status == 'REJECTED':
                    status_txt, color = "❌ ОТКЛОНЕНА", COLOR_DANGER
                elif s.status == 'ERROR':
                    reason = f" ({s.error_reason})" if s.error_reason else ""
                    status_txt, color = f"⛔ ОШИБКА{reason}", COLOR_DANGER
                else:
                    status_txt, color = "⏳ НЕЗАВЕРШЕНА", COLOR_WARNING

                time_s = s.events[0].get("time") if s.events else ""
                date_s = (s.first_date or "")[:5]
                item = QTreeWidgetItem([
                    f"{date_s}  {time_s}  #{s.session_id}", denom_txt, status_txt,
                    s.device_model or '?'
                ])
                for col in range(4):
                    item.setForeground(col, QBrush(color))
                item.setToolTip(2, status_txt)
                date_item.addChild(item)
                tab._payloads[id(item)] = s

        first = tree.topLevelItem(0)
        if first is not None and first.childCount():
            tree.setCurrentItem(first.child(0))
        self._on_lc_select(tab)

    def _on_lc_select(self, tab):
        item = tab.tree.currentItem()
        s = tab._payloads.get(id(item)) if item is not None else None
        if s is None:
            tab.detail.setPlainText("Выберите купюру слева.")
            return
        tab.detail.setPlainText(self._lc_detail(s))

    def _lc_detail(self, s) -> str:
        denom = s.get_display_denom()
        cur = s.currency or 'USD'
        lines = [
            f"КУПЮРА #{s.session_id} · {s.first_date} {s.first_time}",
            "=" * 52,
            f"Статус:        {s.status}",
        ]
        if s.error_reason:
            lines.append(f"Причина:       {s.error_reason}")
        lines += [
            f"Номинал:       ${denom:.0f} {cur}"
            + (f"  (raw {s.denomination})" if s.denomination else ""),
            f"UUID:          {s.uuid}",
            f"Устройство:    {s.device_model} · SN {s.device_serial}",
            f"Закрыта:       {'да' if s.closed else 'нет'}",
            "",
            f"Событий:       {len(s.events)}",
        ]
        for ev in s.events:
            et = (ev.get("event_type") or "").replace(
                "DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_", "")
            dt = ev.get("dt_obj")
            when = dt.strftime("%H:%M:%S") if hasattr(dt, "strftime") else (
                ev.get("time") or "?")
            line = f"   {when}  {et}"
            desc = ev.get("description") or ""
            if desc and desc.lower() not in et.lower():
                line += f"  · {desc[:70]}"
            lines.append(line)
        return "\n".join(lines)

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
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ---- фильтры ----
        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        for text, status in [
            ("🔄 Все", None),
            ("✅ Успешные", 'OK'),
            ("⚠️ Частичные", 'PARTIAL'),
            ("⛔ Ошибки", 'FAILED_ANY'),
            ("⏳ Незавершённые", 'INCOMPLETE'),
            ("⚠️ Подозрительные", 'SUSPICIOUS'),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("compactButton")
            btn.clicked.connect(lambda _, s=status: self._set_w2_filter(tab, s))
            filter_row.addWidget(btn)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        # ---- одна строка: купон + диспенсер + сводка ----
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        top_row.addWidget(QLabel("Купон:"))
        search = QLineEdit()
        search.setPlaceholderText("номер купона")
        search.setClearButtonEnabled(True)
        search.setFixedWidth(300)
        search.setStyleSheet(
            "QLineEdit { background: #2d2e45; color: #e4e5f1;"
            " border: 1px solid #34354e; border-radius: 8px; padding: 6px 10px; }"
            "QLineEdit:focus { border-color: #6366f1; }"
        )
        search.textChanged.connect(lambda _t: self._refresh_w2(tab))
        tab.search = search
        top_row.addWidget(search)

        top_row.addSpacing(6)
        top_row.addWidget(QLabel("Диспенсер:"))
        device_combo = QComboBox()
        device_combo.addItem("Все")
        device_combo.setMinimumWidth(220)
        device_combo.setMaximumWidth(320)
        w2_front: W2FrontendAnalyzer = data['w2_frontend']
        devs = set()
        for s in w2_front.sessions:
            if s.dispenser_device_id:
                label = f"{s.dispenser_model or '?'} ({s.dispenser_device_id})"
                devs.add(label)
        for d in sorted(devs):
            device_combo.addItem(d)
        device_combo.currentIndexChanged.connect(lambda: self._refresh_w2(tab))
        tab.device_combo = device_combo
        top_row.addWidget(device_combo)

        top_row.addStretch()
        tab.info_label = QLabel("Выдач: 0")
        tab.info_label.setStyleSheet("font-weight: 600; color: #9ca3bf;")
        top_row.addWidget(tab.info_label)
        layout.addLayout(top_row)

        # ---- два бокса с отступом вместо сплиттера ----
        content = QHBoxLayout()
        content.setSpacing(10)

        t = _result_theme()
        panel_style = (
            f"QFrame#{0} {{ background: {t['panel']};"
            f" border: 1px solid {t['border']}; border-radius: 10px; }}"
        )

        left_panel = QFrame()
        left_panel.setObjectName("w2ListPanel")
        left_panel.setStyleSheet(
            f"QFrame#w2ListPanel {{ background: {t['panel']};"
            f" border: 1px solid {t['border']}; border-radius: 10px; }}"
        )
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(6, 6, 6, 6)

        tree = QTreeWidget()
        tree.setColumnCount(5)
        tree.setHeaderLabels(["Дата / Время", "Купон / Логин", "Сумма",
                             "Статус", "DS"])
        tree.setRootIsDecorated(False)
        tree.setIndentation(0)
        tree.setUniformRowHeights(True)
        tree.setFrameShape(QFrame.NoFrame)
        tree.setStyleSheet(
            "QTreeWidget { background: transparent; border: none;"
            f" color: {t['text']}; alternate-background-color: transparent; }}"
            "QTreeWidget::item { padding: 5px 6px; }"
            f"QTreeWidget::item:selected {{ background: {t['select']};"
            " color: #ffffff; }"
        )
        tree.setAlternatingRowColors(False)
        tree.itemSelectionChanged.connect(lambda: self._on_w2_select(tab))
        self._install_copy_menu(tree)
        self._setup_tree_header(tree, [175, 115, 95, 100, 45])
        row = QHBoxLayout()
        row.setSpacing(0)
        row.addWidget(tree, 1)
        row.addSpacing(6)
        row.addWidget(self._attach_external_scrollbar(tree))
        left_layout.addLayout(row)
        content.addWidget(left_panel, 48)

        right_panel = QFrame()
        right_panel.setObjectName("w2DetailPanel")
        right_panel.setStyleSheet(
            f"QFrame#w2DetailPanel {{ background: {t['detail']};"
            f" border: 1px solid {t['border']}; border-radius: 10px; }}"
        )
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)

        detail = QTreeWidget()
        detail.setColumnCount(8)
        detail.setHeaderLabels([
            "Событие", "Время", "Купон", "", "", "", "", "Детали"
        ])
        detail.setFrameShape(QFrame.NoFrame)
        detail.setUniformRowHeights(False)
        detail.setWordWrap(True)
        detail.setStyleSheet(
            "QTreeWidget { background: transparent; border: none;"
            f" color: {t['text']}; }}"
            "QTreeWidget::item { padding: 3px 4px; }"
            f"QTreeWidget::item:selected {{ background: {t['select']};"
            " color: #ffffff; }"
        )
        self._setup_tree_header(detail, [250, 70, 80, 0, 0, 0, 0])
        # В карточке деталей средние колонки не нужны — данные в подписи
        for _col in (3, 4, 5, 6):
            detail.setColumnHidden(_col, True)
        detail.header().resizeSection(0, 250)
        detail.header().resizeSection(1, 70)
        detail.header().resizeSection(2, 80)
        self._install_copy_menu(detail)
        self._install_cell_selection(tab, detail, t['select'])
        detail_row = QHBoxLayout()
        detail_row.setSpacing(0)
        detail_row.addWidget(detail, 1)
        detail_row.addSpacing(6)
        detail_row.addWidget(self._attach_external_scrollbar(detail))
        right_layout.addLayout(detail_row)
        tab.detail = detail
        content.addWidget(right_panel, 52)

        layout.addLayout(content, 1)

        tab.tree = tree
        tab._data = data
        tab._payloads = {}
        tab.current_filter = None

        self._refresh_w2(tab, data)
        return tab

    def _set_w2_filter(self, tab, status):
        tab.current_filter = status
        self._refresh_w2(tab)

    def _copy_to_clipboard(self, text: str):
        QGuiApplication.clipboard().setText(text or "")

    def _refresh_w2(self, tab, data=None):
        data = data if data is not None else getattr(tab, "_data", None)
        if not data:
            return
        tree: QTreeWidget = tab.tree
        tree.clear()
        tab._payloads = {}

        w2_front: W2FrontendAnalyzer = data['w2_frontend']
        sessions = list(w2_front.sessions)

        filter_status = getattr(tab, "current_filter", None)
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

        search = getattr(tab, "search", None)
        query = norm_coupon(search.text()) if search else ""
        if query:
            sessions = [
                s for s in sessions
                if query in norm_coupon(getattr(s, "coupon", None))
            ]
            cash_events = []
        else:
            cash_events = list(getattr(w2_front, "cash_events", []) or [])

        total_disp = sum(s.dispensed_amount for s in sessions)
        total_req = sum(s.requested_amount for s in sessions)
        suspicious_shown = sum(1 for s in sessions if s.has_suspicious_match())
        info_text = (f"Выдач: {len(sessions)}   |   "
                     f"выдано ${total_disp/100:,.0f} из ${total_req/100:,.0f}")
        if suspicious_shown:
            info_text += f"   |   ⚠️ {suspicious_shown}"
        if cash_events:
            info_text += f"   |   инкассаций: {len(cash_events)}"
        tab.info_label.setText(info_text)

        if not sessions and not cash_events:
            if query:
                tree.addTopLevelItem(QTreeWidgetItem([
                    "Ничего не найдено по купону", '', '', ''
                ]))
            tab.detail.setPlainText("Нет данных по выбранному фильтру.")
            return

        # ---- хроника: даты по возрастанию, сегодня в конце ----
        items = [(s.start_dt, 'S', s) for s in sessions]
        items += [(e.dt, 'C', e) for e in cash_events]
        groups = defaultdict(list)
        for dt, kind, obj in items:
            key = dt.strftime("%d/%m/%Y") if dt else "Неизвестно"
            groups[key].append((dt or datetime.max, kind, obj))

        def group_sort_key(date_key):
            values = [it[0] for it in groups[date_key]]
            return min(values) if values else datetime.max

        today = datetime.now().strftime("%d/%m/%Y")
        for date in sorted(groups.keys(), key=group_sort_key):
            entries = sorted(groups[date], key=lambda it: it[0])
            sess_list = [o for _, k, o in entries if k == 'S']
            ok_count = sum(1 for s in sess_list if s.status == 'OK')
            day_sum = sum(s.dispensed_amount for s in sess_list
                          if s.status == 'OK') / 100
            title = f'📅 {date}' + (' · сегодня' if date == today else '')
            date_item = QTreeWidgetItem([
                title, f'{ok_count}/{len(sess_list)} ok',
                f'${day_sum:.2f}', ''
            ])
            self._apply_date_style(date_item)
            date_item.setFirstColumnSpanned(False)
            tree.addTopLevelItem(date_item)

            session_idx = 0
            for _dt, kind, obj in entries:
                if kind == 'S':
                    session_idx += 1
                    item = self._w2_session_row(date_item, session_idx, obj)
                    tab._payloads[id(item)] = ('S', obj)
                else:
                    item = self._w2_event_row(date_item, obj)
                    tab._payloads[id(item)] = ('C', obj)

            if len(sessions) + len(cash_events) <= 300:
                date_item.setExpanded(True)

        # выбрать первую строку — детали сразу видно
        first = tree.topLevelItem(0)
        while first is not None and first.childCount():
            if first.child(0) is not None:
                tree.setCurrentItem(first.child(0))
                break
            first = None
        self._on_w2_select(tab)

    @staticmethod
    def _box_counts_text(mapping, limit=12):
        """Кассеты: ключ — номер бокса, значение — количество купюр."""
        if not mapping:
            return ""
        try:
            items = sorted(mapping.items(), key=lambda kv: int(kv[0]))
        except (TypeError, ValueError):
            items = list(mapping.items())
        parts = []
        for box, count in items[:limit]:
            sign = f"{int(count):+d}" if isinstance(count, (int, float)) else str(count)
            parts.append(f"№{box}: {sign}")
        if len(items) > limit:
            parts.append(f"…+{len(items) - limit}")
        return ", ".join(parts)

    @staticmethod
    def _notes_text(mapping, limit=8):
        if not mapping:
            return ""
        try:
            items = sorted(mapping.items(), key=lambda kv: float(kv[0]))
        except (TypeError, ValueError):
            items = list(mapping.items())
        parts = [f"${n}×{c}" for n, c in items[:limit]]
        if len(items) > limit:
            parts.append(f"…+{len(items) - limit}")
        return ", ".join(parts)

    def _cash_event_details(self, e):
        parts = []
        out_notes = self._notes_text(getattr(e, "raw_by_nominal", None))
        if out_notes:
            parts.append(f"OUT: {out_notes}")
        loaded = self._notes_text(getattr(e, "loaded_by_nominal", None))
        if loaded:
            parts.append(f"Загружено: {loaded}")
        rej = self._notes_text(getattr(e, "reject_delta", None))
        if rej:
            parts.append(f"Reject: {rej}")
        boxes = getattr(e, "boxes_delta", None)
        if boxes:
            parts.append(f"Кассеты: {self._box_counts_text(boxes)}")
        return "   |   ".join(parts)

    def _add_device_event_item(self, parent, e):
        """Инкассация/изъятие/пополнение прямо в хронике."""
        dt = e.dt
        date_s = dt.strftime("%d/%m/%Y") if dt else '?'
        time_s = dt.strftime("%H:%M:%S") if dt else '?'
        kind = self.CASH_TYPE_DISPLAY.get(e.event_type, e.event_type)
        out_s = (f"${e.total_cents / 100:.2f}"
                 if getattr(e, "total_cents", 0) else '')
        loaded_s = (f"${e.total_loaded_cents / 100:.2f}"
                    if getattr(e, "total_loaded_cents", 0) else '')
        details = self._cash_event_details(e)
        item = QTreeWidgetItem([
            f"   {date_s}", time_s, kind, out_s, loaded_s, '', '', details
        ])
        item.setToolTip(7, details or kind)
        for col in (2, 3, 4):
            item.setForeground(col, QBrush(COLOR_EVENT))
        parent.addChild(item)
        return item

    @staticmethod
    def _w2_short_status(s) -> str:
        if s.status == 'OK':
            return "✅ ВЫДАНО"
        if s.status == 'PARTIAL':
            return "⚠️ ЧАСТИЧНАЯ"
        if s.status == 'FAILED_COUPON':
            return "❌ КУПОН"
        detail = (s.error_detail or "").lower()
        if "jam" in detail:
            return "⛔ JAM"
        if "timeout" in detail:
            return "⛔ TIMEOUT"
        if "bill-end" in detail or "bill end" in detail or "empty" in detail:
            return "⛔ EMPTY"
        if s.status.startswith('FAILED'):
            return "⛔ ОШИБКА"
        return "⏳ НЕЗАВЕРШЕНА"

    def _w2_session_row(self, parent, idx, s):
        amount = f"${s.dispensed_amount / 100:.2f}"
        if s.requested_amount and s.dispensed_amount < s.requested_amount:
            amount = f"${s.dispensed_amount/100:.2f} / ${s.requested_amount/100:.2f}"
        coupon = getattr(s, "coupon", None) or "—"
        login = s.user_login or "—"
        status_txt = self._w2_short_status(s)
        date_s = s.start_dt.strftime("%d/%m") if s.start_dt else ""
        linked = len(getattr(s, "lowlevel_sessions", []) or [])
        link_txt = f"🔗 {linked}" if linked else "—"
        item = QTreeWidgetItem([
            f"{date_s}  {s.start_time_str}  #{idx}",
            f"{coupon} · {login}",
            amount,
            status_txt,
            link_txt,
        ])
        color = (COLOR_SUCCESS if s.status == 'OK' else
                 COLOR_WARNING if s.status == 'PARTIAL' else
                 COLOR_DANGER if s.status.startswith('FAILED') else COLOR_MUTED)
        for col in range(4):
            item.setForeground(col, QBrush(color))
        if linked:
            item.setForeground(4, QBrush(COLOR_INFO))
        item.setToolTip(4, (f"Склеено с devices_server: {linked} LL-сессий"
                            if linked else "Нет склейки с devices_server"))
        item.setToolTip(1, f"trx {s.trx}")
        full_status = W2_STATUS_DISPLAY.get(s.status, s.status)
        if s.error_detail:
            full_status += f" — {s.error_detail}"
        item.setToolTip(3, full_status)
        parent.addChild(item)
        return item

    def _w2_event_row(self, parent, e):
        dt = e.dt
        time_s = dt.strftime("%H:%M:%S") if dt else '?'
        date_s = dt.strftime("%d/%m") if dt else ""
        kind = self.CASH_TYPE_DISPLAY.get(e.event_type, e.event_type)
        is_in = e.event_type in ("cash_in", "refill")
        amount = (getattr(e, "total_loaded_cents", 0) if is_in
                  else getattr(e, "total_cents", 0))
        if not amount:
            amount = getattr(e, "total_cents", 0) or getattr(e, "total_loaded_cents", 0)
        amount_s = f"${amount / 100:.2f}" if amount else ''
        if is_in:
            status = "IN"
        elif getattr(e, "total_loaded_cents", 0):
            status = "OUT / IN"
        else:
            status = "OUT"
        item = QTreeWidgetItem([f"{date_s}  {time_s}", kind, amount_s, status, ""])
        for col in range(4):
            item.setForeground(col, QBrush(COLOR_EVENT))
        item.setToolTip(1, kind)
        parent.addChild(item)
        return item

    def _on_w2_select(self, tab):
        item = tab.tree.currentItem()
        payload = tab._payloads.get(id(item)) if item is not None else None
        detail = tab.detail
        detail.clear()
        if not payload:
            return
        kind, obj = payload
        if kind == 'S':
            detail.headerItem().setText(2, "Купон")
            detail.setColumnHidden(2, False)
            self._add_w2_session_item(detail.invisibleRootItem(), 1, obj)
        else:
            # у события устройства купона нет — колонку убираем
            detail.headerItem().setText(2, "")
            detail.setColumnHidden(2, True)
            self._add_w2_event_tree(detail, obj)
        detail.expandAll()
        self._clear_cell_selection(tab)

    def _add_w2_event_tree(self, tree, e):
        """Инкассация / изъятие: дерево с раскрывающимися блоками."""
        dt = e.dt
        when = dt.strftime("%d/%m/%Y %H:%M:%S") if dt else "?"
        kind = self.CASH_TYPE_DISPLAY.get(e.event_type, e.event_type)
        date_s = dt.strftime("%d/%m/%Y") if dt else ""
        time_s = dt.strftime("%H:%M:%S") if dt else ""
        root = QTreeWidgetItem([f"{kind} · {date_s}", time_s, "", "", "", "", "", ""])
        for col in range(8):
            root.setForeground(col, QBrush(COLOR_EVENT))
        tree.addTopLevelItem(root)

        is_in = e.event_type in ("cash_in", "refill")
        total_c = getattr(e, "total_cents", 0)
        loaded_c = getattr(e, "total_loaded_cents", 0)
        out_c = 0 if is_in else total_c
        in_c = loaded_c or (total_c if is_in else 0)
        if out_c:
            notes = self._notes_text(getattr(e, "raw_by_nominal", None))
            tree.addTopLevelItem(QTreeWidgetItem([
                "   Изъято (OUT)", "", "", "", "", "", "",
                f"${out_c/100:,.2f}" + (f"   {notes}" if notes else "")
            ]))
        if in_c:
            notes = self._notes_text(getattr(e, "loaded_by_nominal", None))
            tree.addTopLevelItem(QTreeWidgetItem([
                "   Загружено (IN)", "", "", "", "", "", "",
                f"${in_c/100:,.2f}" + (f"   {notes}" if notes else "")
            ]))
        if getattr(e, "reject_delta", None):
            tree.addTopLevelItem(QTreeWidgetItem([
                "   Reject-бокс", "", "", "", "", "", "",
                self._notes_text(e.reject_delta)
            ]))
        if getattr(e, "boxes_delta", None):
            tree.addTopLevelItem(QTreeWidgetItem([
                "   Кассеты (№ бокса)", "", "", "", "", "", "",
                self._box_counts_text(e.boxes_delta)
            ]))
        if getattr(e, "raw_details", ""):
            tree.addTopLevelItem(QTreeWidgetItem([
                "   Подробные данные", "", "", "", "", "", "", e.raw_details
            ]))

    def _w2_session_detail(self, s) -> str:
        lines = []
        dt = s.start_dt.strftime("%d/%m/%Y %H:%M:%S") if s.start_dt else "?"
        lines.append(f"ВЫДАЧА · {dt}")
        lines.append("=" * 56)
        lines.append(f"Статус:        {W2_STATUS_DISPLAY.get(s.status, s.status)}")
        if s.error_detail:
            lines.append(f"Ошибка:        {s.error_detail}")
        lines.append(f"trx:           {s.trx}")
        lines.append(f"Купон:         {getattr(s, 'coupon', None) or '—'}")
        if s.recovery_coupon:
            lines.append(f"Купон возврата:{s.recovery_coupon}")
        lines.append(f"Логин:         {s.user_login or '—'}")
        lines.append("")
        lines.append(f"Запрошено:     ${s.requested_amount/100:,.2f}")
        lines.append(f"Выдано:        ${s.dispensed_amount/100:,.2f}")
        if s.requested_amount:
            lines.append(f"Доля выдачи:   {s.dispensed_amount/s.requested_amount*100:.1f} %")
        lines.append("")
        lines.append(f"Диспенсер:     {s.dispenser_model or '?'} "
                     f"({s.dispenser_device_id or '?'})")
        if s.commands:
            lines.append(f"Команд:        {len(s.commands)}")
            for c in s.commands[:20]:
                try:
                    lines.append(f"   box {c.get('bankIndex')}: "
                                 f"{c.get('notesAmount')} → {c.get('deviceId', '')}")
                except AttributeError:
                    pass
        if s.boxes:
            lines.append("Кассеты:")
            for b in s.boxes[:20]:
                state = "выдано" if b.get('is_rejected') == 2 else (
                    "reject" if b.get('is_rejected') == 1 else "?")
                lines.append(f"   №{b.get('box')} ${b.get('nominal')} × "
                             f"{b.get('count')} ({state})")
        if s.retry_count:
            lines.append(f"Повторов:      {s.retry_count}")
        if s.has_suspicious_match():
            lines.append("")
            lines.append("Подозрительные склейки:")
            for flags in s.suspicion_flags:
                for flag in flags:
                    lines.append(f"   • {flag}")
        lines.append("")
        lines.append(f"LL-сессий:     {len(s.lowlevel_sessions or [])}")
        for ll in (s.lowlevel_sessions or [])[:10]:
            lines.append(f"   {ll.model} reqID={ll.req_id} "
                         f"{ll.status} · выдано {ll.total_dispensed()}")
        if s.events:
            lines.append("")
            lines.append("События:")
            for ev in s.events[:200]:
                when = ev.get('dt')
                when_s = when.strftime("%H:%M:%S") if hasattr(when, "strftime") else "?"
                lines.append(f"   {when_s}  {ev.get('kind', '?')}")
        return "\n".join(lines)

    def _w2_event_detail(self, e) -> str:
        dt = e.dt.strftime("%d/%m/%Y %H:%M:%S") if e.dt else "?"
        kind = self.CASH_TYPE_DISPLAY.get(e.event_type, e.event_type)
        lines = [f"{kind} · {dt}", "=" * 56]
        if getattr(e, "total_cents", 0):
            lines.append(f"Изъято (OUT):   ${e.total_cents/100:,.2f}")
        if getattr(e, "total_loaded_cents", 0):
            lines.append(f"Загружено (IN): ${e.total_loaded_cents/100:,.2f}")
        if getattr(e, "raw_by_nominal", None):
            lines.append(f"OUT по номиналам: {self._notes_text(e.raw_by_nominal)}")
        if getattr(e, "loaded_by_nominal", None):
            lines.append(f"IN по номиналам:  {self._notes_text(e.loaded_by_nominal)}")
        if getattr(e, "reject_delta", None):
            lines.append(f"Reject-бокс:      {self._notes_text(e.reject_delta)}")
        if getattr(e, "boxes_delta", None):
            lines.append(f"Кассеты:          {self._notes_text(e.boxes_delta)}")
        return "\n".join(lines)

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
        sess_item.setToolTip(2, login_display)
        sess_item.setToolTip(6, device_display)
        sess_item.setToolTip(7, details_str)
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
                sess_item.setToolTip(3, s.model or '?')
                sess_item.setToolTip(7, details_str)
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

    @staticmethod
    def _tree_item_text(item: QTreeWidgetItem, depth: int = 0) -> str:
        parts = [item.text(c).strip() for c in range(item.columnCount())]
        parts = [x for x in parts if x]
        line = "   " * depth + "  |  ".join(parts)
        for i in range(item.childCount()):
            line += "\n" + AnalysisResultWindow._tree_item_text(item.child(i), depth + 1)
        return line

    @staticmethod
    def _tree_all_text(tree: QTreeWidget) -> str:
        lines = []
        for i in range(tree.topLevelItemCount()):
            lines.append(AnalysisResultWindow._tree_item_text(tree.topLevelItem(i)))
        return "\n".join(lines)

    def _clear_cell_selection(self, tab):
        state = getattr(tab, "_cell_state", None)
        if state is not None:
            state["cell"] = None
        tab._sel_cell = None
        detail = getattr(tab, "detail", None)
        if detail is not None:
            detail.viewport().update()

    def _install_cell_selection(self, tab, tree: QTreeWidget, color: str):
        """Выделение отдельной ячейки, а не всей строки."""
        tree.setSelectionMode(QAbstractItemView.NoSelection)
        state = {"cell": None}
        tab._cell_state = state
        tree.setItemDelegate(_CellHighlightDelegate(state, color, tree, tree))

        def on_click(item, column):
            if item is None or tree.isColumnHidden(column):
                return
            state["cell"] = (item, column)
            tab._sel_cell = (item, column)
            tree.viewport().update()

        tree.itemClicked.connect(on_click)

    def _install_copy_menu(self, tree: QTreeWidget):
        """Правый клик -> Копировать (строку / выделенное / всё)."""
        tree.setContextMenuPolicy(Qt.CustomContextMenu)
        tree.setSelectionMode(QAbstractItemView.ExtendedSelection)

        def show_menu(pos):
            item = tree.itemAt(pos)
            menu = QMenu(tree)
            col = tree.header().logicalIndexAt(pos.x()) if item else -1
            act_cell = (menu.addAction("Копировать ячейку")
                        if item is not None and col >= 0 else None)
            act_row = menu.addAction("Копировать строку") if item else None
            act_sel = menu.addAction("Копировать выделенное")
            act_all = menu.addAction("Копировать всё")
            chosen = menu.exec(tree.viewport().mapToGlobal(pos))
            if chosen is None:
                return
            if act_cell is not None and chosen == act_cell:
                self._copy_to_clipboard(item.text(col))
            elif act_row is not None and chosen == act_row:
                self._copy_to_clipboard(self._tree_item_text(item))
            elif chosen == act_sel:
                items = tree.selectedItems()
                self._copy_to_clipboard(
                    "\n".join(self._tree_item_text(i) for i in items)
                )
            elif chosen == act_all:
                self._copy_to_clipboard(self._tree_all_text(tree))

        tree.customContextMenuRequested.connect(show_menu)

    @staticmethod
    def _attach_external_scrollbar(tree: QTreeWidget) -> QScrollBar:
        """Выносит вертикальную полосу прокрутки в отдельную зону.

        Внутренняя полоса дерева скрывается, а снаружи ставится своя,
        синхронизированная с ней. Это позволяет поставить полосу ровно
        по центру между таблицей и рамкой бокса.
        """
        inner = tree.verticalScrollBar()
        bar = QScrollBar(Qt.Vertical)
        bar.setFixedWidth(12)
        tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        def sync_range(minimum, maximum):
            bar.setRange(minimum, maximum)
            bar.setPageStep(inner.pageStep())
            bar.setVisible(maximum > minimum)

        inner.rangeChanged.connect(sync_range)
        inner.valueChanged.connect(bar.setValue)
        bar.valueChanged.connect(inner.setValue)
        sync_range(inner.minimum(), inner.maximum())
        return bar

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

        tree.setAlternatingRowColors(True)
        tree.setIndentation(14)

        header = tree.header()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(60)
        col_count = tree.columnCount()

        # Последняя колонка («Детали») забирает остаток ширины, остальные
        # получают заданные ширины. Первая колонка тоже фиксированная —
        # иначе на узких экранах её сжимало до «…».
        for i in range(col_count):
            if i == col_count - 1:
                header.setSectionResizeMode(i, QHeaderView.Stretch)
            elif i < len(section_widths):
                header.setSectionResizeMode(i, QHeaderView.Interactive)
                header.resizeSection(i, section_widths[i])
            else:
                header.setSectionResizeMode(i, QHeaderView.Stretch)

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

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def closeEvent(self, event: QCloseEvent):
        """Данные (devices_data) остаются в ParserPage, окно можно открыть снова."""
        self.visibility_changed.emit(False)
        self.window_closed.emit(self)
        super().closeEvent(event)