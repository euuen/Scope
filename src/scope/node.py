"""ScopeNode —— 前端示波器节点（薄层包装）。

生命周期:
    _init()   → 创建 QApplication 和 ScopeMainWindow
    _ready()  → 显示窗口
    _process()→ 处理 Qt 事件
"""

import asyncio
import sys
import logging
from typing import Optional

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QPalette, QColor

import pyqtgraph as pg

from src.framework import Node
from .window import ScopeMainWindow

logger = logging.getLogger(__name__)


class ScopeNode(Node):
    """前端示波器节点。"""

    def __init__(self, name: str = "ScopeNode"):
        super().__init__(name)
        self._app: Optional[QApplication] = None
        self._window: Optional[ScopeMainWindow] = None

    def _init(self):
        self._app = QApplication.instance()
        if self._app is None:
            self._app = QApplication(sys.argv)
            self._app.setStyle("Fusion")
            self._app.setFont(QFont("Microsoft YaHei UI", 10))
            self._setup_theme()

        self._window = ScopeMainWindow(
            publish_cb=self.publish,
            subscribe_cb=self.subscribe,
        )

    async def _ready(self):
        if self._window:
            self._window.show()

    async def _process(self):
        if self._app:
            self._app.processEvents()
        await asyncio.sleep(0.005)  # 5ms 让步，避免 CPU 空转

    # ─── 主题 ──────────────────────────────────────────────────────

    def _setup_theme(self):
        p = self._app.palette()
        p.setColor(QPalette.Window, QColor(22, 22, 38))
        p.setColor(QPalette.WindowText, QColor(218, 218, 230))
        p.setColor(QPalette.Base, QColor(26, 26, 44))
        p.setColor(QPalette.AlternateBase, QColor(32, 32, 50))
        p.setColor(QPalette.Text, QColor(218, 218, 230))
        p.setColor(QPalette.Button, QColor(40, 40, 58))
        p.setColor(QPalette.ButtonText, QColor(218, 218, 230))
        p.setColor(QPalette.Highlight, QColor(0, 180, 216))
        p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
        self._app.setPalette(p)

        self._app.setStyleSheet("""
            QMainWindow { background: #161626; }
            QFrame#panel {
                background: #1a1a32; border: 1px solid #26264a;
                border-radius: 8px;
            }
            QFrame#controlBar {
                background: #1a1a32; border: 1px solid #26264a;
                border-radius: 8px;
            }
            QFrame#connectionBar {
                background: #14142a; border-bottom: 1px solid #26264a;
            }
            QFrame#statsBar {
                background: #14142a; border: 1px solid #26264a;
                border-radius: 6px;
            }
            QLabel#statLabel {
                color: #a0a0c0; font-size: 9pt; padding: 2px 4px;
            }
            QTreeWidget, QTableWidget {
                background: #181830; border: 1px solid #26264a;
                border-radius: 8px; padding: 4px;
                alternate-background-color: #1c1c36;
                gridline-color: #363658;
            }
            QTreeWidget::item {
                padding: 4px 2px; border-radius: 3px; min-height: 22px;
            }
            QTreeWidget::item:hover { background: #26264a; }
            QTreeWidget::item:selected {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0a4a6a, stop:1 #083a54);
                color: #e0f0ff; font-weight: bold;
            }
            QTreeView::indicator {
                width: 16px; height: 16px;
                border: 1px solid #5a5a7a;
                border-radius: 3px;
                background: #1a1a34;
            }
            QTreeView::indicator:checked {
                background: #00b4d8;
                border: 1px solid #00b4d8;
            }
            QTreeView::indicator:hover {
                border: 1px solid #00b4d8;
            }
            QTableWidget::item {
                padding: 3px 8px; color: #c0c0d0;
                border-right: 1px solid #26264a;
            }
            QTableWidget::item:hover { background: #26264a; }
            QHeaderView::section {
                background: #181834; color: #a0a0c0;
                border-top: none; border-left: none;
                border-right: 1px solid #363658;
                border-bottom: 1px solid #26264a;
                padding: 6px 8px; font-weight: bold;
            }
            QTabWidget::pane {
                border: 1px solid #26264a; border-radius: 6px;
            }
            QTabBar::tab {
                background: #181832; color: #9090b0;
                padding: 10px 24px;
                border: 1px solid #26264a; border-bottom: none;
                border-top-left-radius: 6px; border-top-right-radius: 6px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #1a1a32; color: #00b4d8;
                border-bottom: 2px solid #00b4d8;
            }
            QLineEdit {
                padding: 7px 10px; background: #181832;
                border: 1px solid #363658; border-radius: 6px;
                color: #e0e0f0;
            }
            QLineEdit:focus { border: 1px solid #00b4d8; }
            QPushButton#importBtn {
                padding: 10px 16px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #183454, stop:1 #122844);
                border: 1px solid #00b4d8; border-radius: 6px;
                color: #00b4d8; font-weight: bold;
            }
            QPushButton#importBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #00b4d8, stop:1 #0088aa);
                color: #fff;
            }
            QPushButton#startBtn {
                padding: 8px 24px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a7840, stop:1 #14562e);
                border: 1px solid #208848; border-radius: 6px;
                color: #e0ffe0; font-weight: bold;
            }
            QPushButton#startBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #24a050, stop:1 #1a7840);
            }
            QPushButton#stopBtn {
                padding: 8px 24px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #b42830, stop:1 #7c1820);
                border: 1px solid #c83038; border-radius: 6px;
                color: #ffe0e0; font-weight: bold;
            }
            QPushButton#stopBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #d03040, stop:1 #b42830);
            }
            QPushButton#connectBtn {
                padding: 6px 20px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a7840, stop:1 #14562e);
                border: 1px solid #208848; border-radius: 5px;
                color: #e0ffe0; font-weight: bold;
            }
            QPushButton#connectBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #24a050, stop:1 #1a7840);
            }
            QPushButton#disconnectBtn {
                padding: 6px 20px;
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #b42830, stop:1 #7c1820);
                border: 1px solid #c83038; border-radius: 5px;
                color: #ffe0e0; font-weight: bold;
            }
            QPushButton#disconnectBtn:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #d03040, stop:1 #b42830);
            }
            QPushButton#scanBtn {
                padding: 5px 14px; background: #262648;
                border: 1px solid #363658; border-radius: 4px;
                color: #c0c0d0; font-size: 9pt;
            }
            QPushButton#scanBtn:hover { background: #363658; }
            QPushButton#smallBtn {
                padding: 4px 12px; background: #222240;
                border: 1px solid #363658; border-radius: 4px;
                color: #a0a0c0; font-size: 9pt;
            }
            QPushButton#smallBtn:hover {
                background: #363658; border: 1px solid #00b4d8;
            }
            QPushButton#presetBtn {
                padding: 4px 6px; background: #222240;
                border: 1px solid #363658; border-radius: 4px;
                color: #a0a0c0; font-size: 9pt; font-weight: bold;
            }
            QPushButton#presetBtn:hover {
                background: #00b4d8; color: #0a0a18;
            }
            QPushButton#exportBtn {
                padding: 8px 20px; background: #262648;
                border: 1px solid #363658; border-radius: 6px;
                color: #c0c0d0;
            }
            QPushButton#exportBtn:hover { background: #363658; }
            QComboBox {
                padding: 5px 10px; background: #181832;
                border: 1px solid #363658; border-radius: 5px;
                color: #e0e0f0;
            }
            QComboBox:focus { border: 1px solid #00b4d8; }
            QComboBox QAbstractItemView {
                background: #1a1a34; border: 1px solid #26264a;
                color: #e0e0f0; selection-background-color: #00b4d8;
                selection-color: #0a0a18;
            }
            QSpinBox {
                padding: 5px 8px; background: #181832;
                border: 1px solid #363658; border-radius: 5px;
                color: #e0e0f0;
            }
            QStatusBar {
                background: #12122a; border-top: 1px solid #26264a;
                color: #a0a0c0;
            }
            QMenuBar { background: #14142c; border-bottom: 1px solid #26264a; color: #c0c0d0; }
            QMenuBar::item:selected { background: #262648; border-radius: 4px; }
            QMenu {
                background: #1a1a34; border: 1px solid #26264a; color: #c0c0d0;
            }
            QMenu::item:selected { background: #00b4d8; color: #0a0a18; }
            QSplitter::handle { background: #22224a; }
            QScrollBar:vertical {
                background: #14142c; width: 8px; border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #363658; min-height: 30px; border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover { background: #4a4a6a; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

        pg.setConfigOptions(
            background=(22, 22, 38),
            foreground=(190, 190, 210),
            antialias=True,
        )