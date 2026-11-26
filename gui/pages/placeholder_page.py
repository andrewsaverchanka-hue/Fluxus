"""Временная страница-заглушка, пока не готовы настоящие страницы."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel


class PlaceholderPage(QWidget):
    """Простая страница с заголовком и текстом «в разработке»."""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(25, 20, 25, 20)
        layout.setSpacing(10)

        title_label = QLabel(title)
        title_label.setObjectName("pageTitle")
        layout.addWidget(title_label)

        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("pageSubtitle")
            subtitle_label.setWordWrap(True)
            layout.addWidget(subtitle_label)

        placeholder = QLabel("🚧 Эта страница в разработке")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("""
            font-size: 14pt;
            color: #9ca3bf;
            padding: 50px;
        """)
        layout.addWidget(placeholder)

        layout.addStretch()