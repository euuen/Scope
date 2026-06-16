"""ScopeMainWindow —— 示波器主窗口。"""

import csv
import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QSpinBox, QLabel, QFileDialog, QStatusBar,
    QMenu, QMessageBox, QApplication, QLineEdit, QHeaderView,
    QTreeWidget, QTreeWidgetItem, QFrame,
    QAbstractItemView, QComboBox, QStyledItemDelegate,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QGraphicsProxyWidget

import pyqtgraph as pg

from src.framework import CloseApplication
from .events import (
    ImportElfRequest, ScanProbesRequest, ConnectProbeRequest,
    DisconnectProbeRequest, SelectVariableRequest,
    StartSamplingRequest, ChangeSampleRateRequest, StopSamplingRequest,
    VariableWriteRequest, PauseMcuRequest, ResumeMcuRequest, ResetSamplingRequest,
    ElfLoaded, ElfLoadFailed, ProbeScanResult,
    ProbeConnected, ProbeDisconnected, ProbeConnectionFailed,
    SampleData, SamplingStatus,
)
from .helpers import (
    COLORS, PRESET_FRAME_RATES, DEFAULT_FRAME_RATE, DEFAULT_TIME_WINDOW,
    ROLE_PATH, ROLE_ADDR, ROLE_TYPE, format_type, is_base_type,
)
from src.typedefs.type_utils import resolve_type

logger = logging.getLogger(__name__)


class ValueEditDelegate(QStyledItemDelegate):
    """变量树「数值」列的自定义编辑器——大号等宽字体，深色主题。"""

    def __init__(self, parent=None, connected_getter=None):
        super().__init__(parent)
        self._connected_getter = connected_getter

    def createEditor(self, parent, option, index):
        if self._connected_getter and not self._connected_getter():
            return None
        editor = QLineEdit(parent)
        editor.setStyleSheet(
            "font-size: 11pt;"
            "font-family: 'Consolas', 'Courier New', monospace;"
            "padding: 6px 10px;"
            "min-height: 30px;"
            "background: #1a1a34;"
            "border: 2px solid #00b4d8;"
            "border-radius: 4px;"
            "color: #e0f0ff;"
        )
        return editor

    def setEditorData(self, editor, index):
        editor.setText(index.data(Qt.DisplayRole))
        editor.selectAll()


class ScopeMainWindow(QMainWindow):
    """示波器主窗口。通过 ScopeNode 与事件总线桥接。"""

    def __init__(self, publish_cb, subscribe_cb):
        super().__init__()
        self._publish = publish_cb
        self._subscribe = subscribe_cb

        self.setWindowTitle("LoopMaster Scope — MCU Variable Oscilloscope")
        self.resize(1500, 860)
        self.setMinimumSize(1100, 600)

        # ── 状态 ──
        self._elf_path: Path | None = None
        self._elf_variables = []
        self._monitored_vars: set[str] = set()
        self._var_registry: dict[str, tuple] = {}
        self._probes: list[dict] = []
        self._is_connected = False
        self._sampling_active = False
        self._mcu_paused = False
        self._sample_rate = 100
        self._frame_rate = DEFAULT_FRAME_RATE
        self._auto_scroll = True
        self._plot_curves: dict[str, pg.PlotDataItem] = {}
        self._visible_vars: set[str] = set()
        self._pack_path: str | None = None
        self._tree_item_by_path: dict[str, QTreeWidgetItem] = {}
        self._sample_buffers: dict[str, tuple[list, list]] = {}
        self._actual_sample_rate: float = 0
        self._updating_tree_values = False

        # ── 订阅事件 ──
        self._subscribe(ElfLoaded, self._on_elf_loaded)
        self._subscribe(ElfLoadFailed, self._on_elf_load_failed)
        self._subscribe(ProbeScanResult, self._on_probe_scan_result)
        self._subscribe(ProbeConnected, self._on_probe_connected)
        self._subscribe(ProbeDisconnected, self._on_probe_disconnected)
        self._subscribe(ProbeConnectionFailed, self._on_probe_connection_failed)
        self._subscribe(SampleData, self._on_sample_data)
        self._subscribe(SamplingStatus, self._on_sampling_status)

        # ── 构建 UI ──
        self._setup_shortcuts()
        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()

        # ── 定时器 ──
        self._plot_timer = QTimer()
        self._plot_timer.timeout.connect(self._update_plot)
        self._plot_timer.setInterval(int(1000 / DEFAULT_FRAME_RATE))
        self._plot_timer.start()

    # ═══════════════════════════════════════════════════════════════
    # 菜单
    # ═══════════════════════════════════════════════════════════════

    def _setup_menu(self):
        file_menu = self.menuBar().addMenu("文件(&F)")

        action = QAction("导入 ELF/AXF...", self)
        action.triggered.connect(self._on_import_elf)
        file_menu.addAction(action)

        action = QAction("导入 CMSIS-Pack...", self)
        action.triggered.connect(self._on_import_pack)
        file_menu.addAction(action)

        file_menu.addSeparator()

        action = QAction("退出", self)
        action.triggered.connect(self.publish_close_app)
        file_menu.addAction(action)

        probe_menu = self.menuBar().addMenu("探针(&P)")

        action = QAction("扫描探针", self)
        action.triggered.connect(self._on_scan)
        probe_menu.addAction(action)

        action = QAction("连接", self)
        action.triggered.connect(self._on_connect)
        probe_menu.addAction(action)

        action = QAction("断开", self)
        action.triggered.connect(self._on_disconnect)
        probe_menu.addAction(action)

        display_menu = self.menuBar().addMenu("显示(&D)")
        for fps in PRESET_FRAME_RATES:
            action = QAction(f"{fps} FPS", self)
            action.setCheckable(True)
            action.setChecked(fps == DEFAULT_FRAME_RATE)
            action.setData(fps)
            action.triggered.connect(lambda checked, f=fps: self._set_frame_rate(f))
            display_menu.addAction(action)

    # ═══════════════════════════════════════════════════════════════
    # 快捷键
    # ═══════════════════════════════════════════════════════════════

    def _setup_shortcuts(self):
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self._on_import_elf)
        QShortcut(QKeySequence("F5"), self).activated.connect(self._on_connect)
        QShortcut(QKeySequence("Space"), self).activated.connect(self._on_sampling_clicked)

    # ═══════════════════════════════════════════════════════════════
    # 状态栏
    # ═══════════════════════════════════════════════════════════════

    def _setup_statusbar(self):
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._status_led = QLabel("●")
        self._status_led.setStyleSheet("color: #e04040; font-size: 14px;")
        self._status_bar.addWidget(self._status_led)

        self._status_info = QLabel("  探针: 未连接  |  目标: --")
        self._status_bar.addWidget(self._status_info)

        self._status_rate = QLabel("  |  —")
        self._status_rate.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 10pt;"
        )
        self._status_bar.addPermanentWidget(self._status_rate)

    # ═══════════════════════════════════════════════════════════════
    # 连接栏
    # ═══════════════════════════════════════════════════════════════

    def _build_connection_bar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("connectionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(8)

        layout.addWidget(QLabel("🔌"))

        self._probe_selector = QComboBox()
        self._probe_selector.setMinimumWidth(220)
        self._probe_selector.setPlaceholderText("点击扫描发现探针...")
        layout.addWidget(self._probe_selector)

        self._scan_btn = QPushButton("🔄 扫描")
        self._scan_btn.setObjectName("scanBtn")
        self._scan_btn.setFixedHeight(30)
        self._scan_btn.clicked.connect(self._on_scan)
        layout.addWidget(self._scan_btn)

        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFixedWidth(1)
        layout.addWidget(separator)

        layout.addWidget(QLabel("模式:"))
        self._mode_selector = QComboBox()
        self._mode_selector.setFixedWidth(130)
        self._mode_selector.addItems(["复位启动", "附加启动"])
        layout.addWidget(self._mode_selector)

        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFixedWidth(1)
        layout.addWidget(separator)

        layout.addWidget(QLabel("SWD:"))
        self._swd_speed_selector = QComboBox()
        self._swd_speed_selector.setFixedWidth(85)
        self._swd_speed_selector.addItems(["1 MHz", "4 MHz", "10 MHz"])
        self._swd_speed_selector.setCurrentIndex(1)
        layout.addWidget(self._swd_speed_selector)

        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFixedWidth(1)
        layout.addWidget(separator)

        layout.addWidget(QLabel("目标:"))
        self._target_input = QLineEdit()
        self._target_input.setText("stm32f407ig")
        self._target_input.setFixedWidth(130)
        self._target_input.setToolTip(
            "强制指定目标芯片型号，如 stm32f407ig。留空则由 pyOCD 自动识别。"
        )
        layout.addWidget(self._target_input)

        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFixedWidth(1)
        layout.addWidget(separator)

        self._connect_btn = QPushButton("连接")
        self._connect_btn.setObjectName("connectBtn")
        self._connect_btn.setFixedHeight(30)
        self._connect_btn.clicked.connect(self._on_connect)
        layout.addWidget(self._connect_btn)

        self._conn_indicator = QLabel("●")
        self._conn_indicator.setStyleSheet(
            "color: #e04040; font-size: 16px; padding: 0 4px;"
        )
        layout.addWidget(self._conn_indicator)

        self._conn_status_label = QLabel("未连接")
        self._conn_status_label.setStyleSheet("color: #8080a0; font-size: 9pt;")
        layout.addWidget(self._conn_status_label)

        layout.addStretch()
        return bar

    # ═══════════════════════════════════════════════════════════════
    # 主界面搭建
    # ═══════════════════════════════════════════════════════════════

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_connection_bar())

        self._main_splitter = QSplitter(Qt.Horizontal)

        # ── 左面板: 变量树 ────────────────────────────────────────
        self._build_left_panel()

        # ── 右面板: 波形 + 控制栏 ─────────────────────────────────
        self._build_right_panel()

        self._main_splitter.setSizes([440, 1060])
        root_layout.addWidget(self._main_splitter, stretch=1)

        # 启动后自动扫描探针
        QTimer.singleShot(500, self._on_scan)

    # ── 左面板 ────────────────────────────────────────────────────

    def _build_left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 6, 8)
        layout.setSpacing(6)

        # 顶部按钮行
        toolbar = QHBoxLayout()
        self._import_btn = QPushButton("📂  导入 ELF")
        self._import_btn.setObjectName("importBtn")
        self._import_btn.clicked.connect(self._on_import_elf)
        toolbar.addWidget(self._import_btn)

        self._filter_input = QLineEdit()
        self._filter_input.setPlaceholderText("🔍  搜索变量...")
        self._filter_input.setClearButtonEnabled(True)
        self._filter_input.textChanged.connect(self._on_filter)
        toolbar.addWidget(self._filter_input, stretch=1)
        layout.addLayout(toolbar)

        # 统计栏
        self._build_stats_bar(layout)

        # 变量树
        self._build_var_tree(layout)

        # 空状态提示
        self._empty_state_label = QLabel(
            "<div style='text-align:center; padding:40px;'>"
            "<div style='font-size:40px;'>📂</div>"
            "<div style='font-size:14px; color:#8080a0; margin-top:8px;'>"
            "导入 ELF 文件开始使用</div>"
            "<div style='font-size:11px; color:#606080; margin-top:4px;'>"
            "Ctrl+E 快速导入</div></div>"
        )
        self._empty_state_label.setAlignment(Qt.AlignCenter)
        self._empty_state_label.setObjectName("emptyState")
        self._empty_state_label.setVisible(False)
        layout.addWidget(self._empty_state_label)

        layout.addWidget(
            QLabel("💡 勾选要监控的变量 · 展开结构体可勾选子成员")
        )

        self._main_splitter.addWidget(panel)

    def _build_stats_bar(self, parent_layout):
        bar = QFrame()
        bar.setObjectName("statsBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 4, 10, 4)
        bar_layout.setSpacing(12)

        self._stat_total_vars = QLabel("📊  变量: 0")
        self._stat_total_vars.setObjectName("statLabel")
        bar_layout.addWidget(self._stat_total_vars)

        self._stat_total_files = QLabel("📁  文件: 0")
        self._stat_total_files.setObjectName("statLabel")
        bar_layout.addWidget(self._stat_total_files)

        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFixedWidth(1)
        bar_layout.addWidget(separator)

        self._stat_selected_vars = QLabel("✅  已选: 0")
        self._stat_selected_vars.setObjectName("statLabel")
        bar_layout.addWidget(self._stat_selected_vars)

        bar_layout.addStretch()
        parent_layout.addWidget(bar)

    def _build_var_tree(self, parent_layout):
        self._var_tree = QTreeWidget()
        self._var_tree.setHeaderLabels(["变量名", "数值", "类型"])
        header = self._var_tree.header()

        # 全部 Interactive，我们自己控制宽度分配
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(40)

        self._col_name_width = 140
        self._col_value_width = 140
        self._col_type_width = 140
        self._var_tree.setColumnWidth(0, self._col_name_width)
        self._var_tree.setColumnWidth(1, self._col_value_width)
        self._var_tree.setColumnWidth(2, self._col_type_width)

        # 用户拖拽分隔线改变列宽
        self._tree_resize_pending = False

        def _on_section_resized(idx, old, new):
            if self._tree_resize_pending:
                return
            if idx == 0:
                self._col_name_width = new                 # col0 新宽度
            elif idx == 1:
                self._col_value_width = new                # col1 新宽度
            elif idx == 2:
                self._col_type_width = new                 # col2 新宽度
            self._tree_resize_pending = True
            QTimer.singleShot(0, lambda i=idx: self._recalculate_columns(i))

        header.sectionResized.connect(_on_section_resized)

        # 容器缩放时三列按比例缩放
        orig_resize = self._var_tree.resizeEvent

        def _on_resize(ev):
            orig_resize(ev)
            self._resize_tree_proportionally()

        self._var_tree.resizeEvent = _on_resize

        self._var_tree.setIndentation(18)
        self._var_tree.setAnimated(True)
        self._var_tree.setAlternatingRowColors(True)
        self._var_tree.setSelectionMode(QAbstractItemView.NoSelection)
        self._var_tree.setItemDelegateForColumn(1, ValueEditDelegate(self._var_tree, lambda: self._is_connected))

        # 默认委托：只允许数值列（列 1）编辑，且必须已连接设备
        class _ColumnRestrictedDelegate(QStyledItemDelegate):
            def createEditor(self, parent, option, index):
                if index.column() == 1 and self._is_connected:
                    return super().createEditor(parent, option, index)
                return None
        self._var_tree.setItemDelegate(_ColumnRestrictedDelegate(self._var_tree))
        self._var_tree.itemChanged.connect(self._on_item_changed)
        parent_layout.addWidget(self._var_tree, stretch=1)

    # ── 右面板 ────────────────────────────────────────────────────

    def _build_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 8, 8, 8)
        layout.setSpacing(6)

        # 波形图
        self._plot_widget = pg.GraphicsLayoutWidget()
        self._plot_view = self._plot_widget.addPlot()
        self._plot_view.setLabel("left", "数值", color="#b0b0b0")
        self._plot_view.setLabel("bottom", "时间", units="s", color="#b0b0b0")
        self._plot_view.showGrid(x=True, y=True, alpha=0.12)

        legend = self._plot_view.addLegend()
        if legend:
            legend.setBrush(pg.mkBrush(30, 30, 45, 200))
            legend.setPen(pg.mkPen(color=(80, 80, 100), width=1))

        self._plot_view.setAutoVisible(y=True)
        self._plot_view.getAxis("left").setPen(pg.mkPen(color="#606060"))
        self._plot_view.getAxis("bottom").setPen(pg.mkPen(color="#606060"))
        self._plot_view.setMouseEnabled(x=True, y=True)
        self._plot_view.enableAutoRange(y=True)
        self._plot_view.getViewBox().sigRangeChangedManually.connect(
            self._on_user_interact
        )

        # Y 轴自动按钮（叠加在图上的小控件）
        self._y_auto_btn = QPushButton("Y自动")
        self._y_auto_btn.setCheckable(True)
        self._y_auto_btn.setChecked(True)
        self._y_auto_btn.setFixedSize(46, 22)
        self._y_auto_btn.setStyleSheet(
            "QPushButton { background: rgba(30,30,56,200); border:1px solid #4a4a6a;"
            " border-radius:4px; color:#a0a0c0; font-size:8pt; font-weight:bold; }"
            "QPushButton:checked { background: rgba(0,180,216,180); color:#0a0a18; }"
            "QPushButton:hover { border:1px solid #00b4d8; }"
        )
        self._y_auto_btn.toggled.connect(self._on_y_toggled)

        proxy = QGraphicsProxyWidget()
        proxy.setWidget(self._y_auto_btn)
        self._plot_view.getViewBox().scene().addItem(proxy)
        proxy.setZValue(100)
        self._plot_view.getViewBox().sigResized.connect(
            lambda: proxy.setPos(self._plot_view.getViewBox().width() - 52, 8)
        )

        layout.addWidget(self._plot_widget, stretch=1)

        # 控制栏
        self._build_control_bar(layout)

        self._main_splitter.addWidget(panel)

    def _build_control_bar(self, parent_layout):
        bar = QFrame()
        bar.setObjectName("controlBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        layout.addWidget(QLabel("采样率:"))
        self._sample_rate_selector = QComboBox()
        self._sample_rate_selector.setFixedWidth(90)
        for label, val in [
            ("1 Hz", 1), ("10 Hz", 10), ("50 Hz", 50),
            ("100 Hz", 100), ("200 Hz", 200),
            ("500 Hz", 500), ("1000 Hz", 1000), ("MAX", 0),
        ]:
            self._sample_rate_selector.addItem(label, val)
        self._sample_rate_selector.setCurrentIndex(3)
        self._sample_rate_selector.currentIndexChanged.connect(self._on_sample_rate_changed)
        layout.addWidget(self._sample_rate_selector)

        layout.addSpacing(12)
        layout.addWidget(QLabel("窗口:"))
        self._time_window_spin = QSpinBox()
        self._time_window_spin.setRange(1, 120)
        self._time_window_spin.setSuffix(" s")
        self._time_window_spin.setFixedWidth(70)
        self._time_window_spin.setValue(DEFAULT_TIME_WINDOW)
        layout.addWidget(self._time_window_spin)

        for tw in [5, 10, 30, 60]:
            preset_btn = QPushButton(f"{tw}s")
            preset_btn.setObjectName("presetBtn")
            preset_btn.setFixedWidth(34)
            preset_btn.clicked.connect(
                lambda checked, t=tw: self._time_window_spin.setValue(t)
            )
            layout.addWidget(preset_btn)

        layout.addStretch()

        self._sampling_btn = QPushButton("开始采样")
        self._sampling_btn.setObjectName("startBtn")
        self._sampling_btn.clicked.connect(self._on_sampling_clicked)
        layout.addWidget(self._sampling_btn)

        self._mcu_btn = QPushButton("暂停运行")
        self._mcu_btn.setObjectName("mcuRunBtn")
        self._mcu_btn.clicked.connect(self._on_mcu_clicked)
        self._mcu_btn.setEnabled(False)
        layout.addWidget(self._mcu_btn)

        self._clear_points_btn = QPushButton("清除点")
        self._clear_points_btn.setObjectName("clearPointsBtn")
        self._clear_points_btn.clicked.connect(self._on_clear_points)
        layout.addWidget(self._clear_points_btn)

        self._reset_btn = QPushButton("复位")
        self._reset_btn.setObjectName("resetBtn")
        self._reset_btn.clicked.connect(self._on_reset)
        layout.addWidget(self._reset_btn)

        self._export_btn = QPushButton("导出 CSV")
        self._export_btn.setObjectName("exportBtn")
        self._export_btn.clicked.connect(self._on_export)
        layout.addWidget(self._export_btn)

        parent_layout.addWidget(bar)

    # ═══════════════════════════════════════════════════════════════
    # 菜单动作
    # ═══════════════════════════════════════════════════════════════

    def publish_close_app(self):
        self._publish(CloseApplication())

    def _on_import_elf(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 ELF/AXF", "", "ELF/AXF (*.elf *.axf *.out);;All (*.*)"
        )
        if path:
            self._publish(ImportElfRequest(path=path))

    def _on_import_pack(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 CMSIS-Pack", "", "Pack (*.pack *.pdsc);;All (*.*)"
        )
        if path:
            self._pack_path = path

    def _on_scan(self):
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText("扫描中...")
        QApplication.processEvents()
        self._publish(ScanProbesRequest())

    def _on_connect(self):
        if self._is_connected:
            self._publish(DisconnectProbeRequest())
            return
        if not self._probes:
            QMessageBox.warning(self, "提示", "请先扫描探针。")
            return
        probe_idx = self._probe_selector.currentIndex()
        if probe_idx < 0 or probe_idx >= len(self._probes):
            return

        mode = "reset" if "复位" in self._mode_selector.currentText() else "attach"
        freq_text = self._swd_speed_selector.currentText()
        freq = int(freq_text.split()[0]) * 1_000_000
        target_override = self._target_input.text().strip()
        self._publish(
            ConnectProbeRequest(
                probe_index=probe_idx, mode=mode, swd_freq_hz=freq,
                target_override=target_override,
            )
        )

    def _on_disconnect(self):
        self._publish(DisconnectProbeRequest())

    def _on_sample_rate_changed(self, index):
        """下拉采样率变化时立即发布事件。"""
        rate = self._sample_rate_selector.itemData(index)
        if rate == 0:
            rate = 1000
        self._publish(ChangeSampleRateRequest(sample_rate_hz=rate))

    def _on_sampling_clicked(self):
        """采样按钮：开始采样 / 停止采样 / 恢复采样。

        按钮文字由 _on_sampling_status 维护，依据文本区分行为：
          - 「停止采样」→ 暂停采集，保留缓冲区
          - 「恢复采样」→ 从当前时刻继续，保留旧数据
          - 「开始采样」→ 清空所有旧数据后全新启动
        """
        btn_text = self._sampling_btn.text()

        if btn_text == "停止采样":
            # 采样中 → 暂停
            self._publish(StopSamplingRequest())
            self._auto_scroll = False
            return

        if not self._monitored_vars:
            QMessageBox.warning(self, "提示", "请先在左侧选择要监控的变量。")
            return

        rate = self._sample_rate_selector.currentData()
        if rate == 0:
            rate = 1000

        if btn_text == "开始采样":
            # 开始采样(全新)：清空后端 + 前端再启动
            self._publish(ResetSamplingRequest())
            self._sample_buffers.clear()
            self._plot_curves.clear()
            self._plot_view.clear()
            legend = self._plot_view.addLegend()
            if legend:
                legend.setBrush(pg.mkBrush(30, 30, 45, 200))
                legend.setPen(pg.mkPen(color=(80, 80, 100), width=1))

        # btn_text == "恢复采样" or "开始采样" → 启动/恢复
        self._publish(StartSamplingRequest(sample_rate_hz=rate))
        self._auto_scroll = True

    def _on_mcu_clicked(self):
        """「暂停运行」/「恢复运行」按钮。

        暂停：冻结 MCU 时间（时间刻度暂停），波形图停止滚动。
        恢复：MCU 时间从暂停点继续，波形图继续滚动。
        """
        if self._mcu_paused:
            self._publish(ResumeMcuRequest())
        else:
            self._publish(PauseMcuRequest())

    def _on_reset(self):
        """复位按钮：清空所有数据。

        如果在采样状态下点击，清空后自动重新开始采样。
        """
        was_sampling = self._sampling_active

        if self._sampling_active:
            self._publish(StopSamplingRequest())

        self._publish(ResetSamplingRequest())

        self._sample_buffers.clear()
        self._plot_curves.clear()
        self._plot_view.clear()

        legend = self._plot_view.addLegend()
        if legend:
            legend.setBrush(pg.mkBrush(30, 30, 45, 200))
            legend.setPen(pg.mkPen(color=(80, 80, 100), width=1))

        self._sampling_active = False
        self._mcu_paused = False
        self._actual_sample_rate = 0

        self._sampling_btn.setText("开始采样")
        self._sampling_btn.setObjectName("startBtn")
        self._sampling_btn.style().unpolish(self._sampling_btn)
        self._sampling_btn.style().polish(self._sampling_btn)
        self._mcu_btn.setEnabled(False)
        self._mcu_btn.setText("暂停运行")
        self._mcu_btn.setObjectName("mcuRunBtn")

        self._status_rate.setText("  |  —")
        self._status_info.setText("  已复位")

        # 复位前在采样中 → 自动重新开始采样
        if was_sampling and self._monitored_vars:
            QTimer.singleShot(50, self._on_sampling_clicked)

    def _on_clear_points(self):
        """清除已采集的数据点，不停止采样。

        采样中时清空缓冲区，新数据继续流动并自动重建曲线。
        暂停时仅清空缓冲区，按钮变为「开始采样」。
        """
        self._sample_buffers.clear()
        self._plot_curves.clear()
        self._plot_view.clear()

        legend = self._plot_view.addLegend()
        if legend:
            legend.setBrush(pg.mkBrush(30, 30, 45, 200))
            legend.setPen(pg.mkPen(color=(80, 80, 100), width=1))

        # 如果暂停中且无数据了，按钮切回「开始采样」
        if not self._sampling_active and not self._sample_buffers:
            self._sampling_btn.setText("开始采样")
            self._sampling_btn.setObjectName("startBtn")
            self._sampling_btn.style().unpolish(self._sampling_btn)
            self._sampling_btn.style().polish(self._sampling_btn)

        self._status_info.setText("  已清除数据点")

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "scope_data.csv", "CSV (*.csv)"
        )
        if not path:
            return

        if self._sample_buffers:
            # 有采样数据 → 导出时间序列
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                names = sorted(self._sample_buffers.keys())
                writer.writerow(["时间戳"] + names)
                max_len = max(
                    len(self._sample_buffers[n][1]) for n in names
                )
                timestamps = list(self._sample_buffers[names[0]][0])
                for i in range(max_len):
                    row = [timestamps[i] if i < len(timestamps) else ""]
                    for n in names:
                        vals = self._sample_buffers[n][1]
                        row.append(
                            f"{vals[i]:.4g}" if i < len(vals) else ""
                        )
                    writer.writerow(row)
            self._status_info.setText(f"  已导出 → {Path(path).name}")
        else:
            # 无采样数据 → 导出变量清单
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["变量", "地址", "类型"])
                for var_path, (addr, type_info) in self._var_registry.items():
                    writer.writerow(
                        [var_path, f"0x{addr:08X}", format_type(type_info)]
                    )
            self._status_info.setText(f"  已导出变量清单 → {Path(path).name}")

    # ═══════════════════════════════════════════════════════════════
    # 事件响应
    # ═══════════════════════════════════════════════════════════════

    def _on_elf_loaded(self, event: ElfLoaded):
        self._elf_path = Path(event.path)
        self._elf_variables = event.variables or []
        self._elf_values = event.values or {}  # { path: initial_value, ... }
        self._var_registry.clear()
        self._tree_item_by_path.clear()
        self._populate_tree()
        self._update_stats()
        self.setWindowTitle(f"LoopMaster Scope — {self._elf_path.name}")
        self._status_info.setText(f"  已加载: {self._elf_path.name}")

    def _on_elf_load_failed(self, event: ElfLoadFailed):
        QMessageBox.warning(self, "加载失败", event.reason)

    def _on_probe_scan_result(self, event: ProbeScanResult):
        self._scan_btn.setEnabled(True)
        self._scan_btn.setText("🔄 扫描")
        self._probes = event.probes or []
        self._probe_selector.clear()
        if not self._probes:
            self._probe_selector.addItem("— 未找到探针 —")
            self._conn_status_label.setText("未找到探针")
        else:
            for p in self._probes:
                uid = (p.get("uid") or "????")[:8]
                name = p.get("name") or "Unknown"
                vendor = p.get("vendor") or ""
                self._probe_selector.addItem(
                    f"{name} ({vendor}) [{uid}]"
                )
            self._conn_status_label.setText(
                f"找到 {len(self._probes)} 个探针"
            )
            self._probe_selector.setCurrentIndex(0)

    def _on_probe_connected(self, event: ProbeConnected):
        self._is_connected = True
        self._conn_indicator.setStyleSheet("color: #40e060; font-size: 16px;")
        self._conn_status_label.setText("已连接")
        self._conn_status_label.setStyleSheet(
            "color: #40e060; font-weight: bold;"
        )
        self._connect_btn.setText("断开")
        self._connect_btn.setObjectName("disconnectBtn")
        self._connect_btn.style().unpolish(self._connect_btn)
        self._connect_btn.style().polish(self._connect_btn)
        self._status_led.setStyleSheet("color: #40e060; font-size: 14px;")
        self._status_info.setText(
            f"  探针: {event.probe_name}  |  目标: {event.target_name}"
        )
        # 连接后重置为「开始采样」状态
        self._sampling_btn.setText("开始采样")
        self._sampling_btn.setObjectName("startBtn")
        self._sampling_btn.style().unpolish(self._sampling_btn)
        self._sampling_btn.style().polish(self._sampling_btn)

    def _on_probe_disconnected(self, event: ProbeDisconnected):
        self._is_connected = False
        self._conn_indicator.setStyleSheet("color: #e04040; font-size: 16px;")
        self._conn_status_label.setText("未连接")
        self._conn_status_label.setStyleSheet("color: #8080a0;")
        self._connect_btn.setText("连接")
        self._connect_btn.setObjectName("connectBtn")
        self._connect_btn.style().unpolish(self._connect_btn)
        self._connect_btn.style().polish(self._connect_btn)
        self._status_led.setStyleSheet("color: #e04040; font-size: 14px;")
        self._status_info.setText("  探针: 未连接  |  目标: --")

        # 断开探针时同时停止采样，重置采样按钮状态
        if self._sampling_active:
            self._publish(StopSamplingRequest())
            self._sampling_active = False
        self._mcu_paused = False
        self._sampling_btn.setText("开始采样")
        self._sampling_btn.setObjectName("startBtn")
        self._sampling_btn.style().unpolish(self._sampling_btn)
        self._sampling_btn.style().polish(self._sampling_btn)
        self._mcu_btn.setText("暂停运行")
        self._mcu_btn.setObjectName("mcuRunBtn")
        self._mcu_btn.setEnabled(False)

    def _on_probe_connection_failed(self, event: ProbeConnectionFailed):
        self._scan_btn.setEnabled(True)
        QMessageBox.warning(self, "连接失败", event.reason)

    def _on_sample_data(self, event: SampleData):
        buffers = event.buffers or {}
        new_ts = list(event.timestamps or [])

        if not new_ts or not buffers:
            return

        t = new_ts[0]  # 单个时间戳
        max_buf = self._sample_rate * 300

        for path, val in buffers.items():
            if path in self._sample_buffers:
                old_ts, old_vals = self._sample_buffers[path]
                old_ts.append(t)
                old_vals.append(val)
                if len(old_ts) > max_buf:
                    self._sample_buffers[path] = (
                        old_ts[-max_buf:], old_vals[-max_buf:]
                    )
            else:
                self._sample_buffers[path] = (
                    [t], [val]
                )

            # 创建波形曲线（首次出现时）
            if path not in self._plot_curves:
                color = COLORS[len(self._plot_curves) % len(COLORS)]
                curve = self._plot_view.plot(
                    [], [],
                    pen=pg.mkPen(color=color, width=1.5),
                    name=path,
                    connect='finite',
                    autoDownsample=True,
                    clipToView=True,
                )
                self._plot_curves[path] = curve

            # 后端发一个点就立即画一个点——不等待定时器批量重绘
            curve = self._plot_curves[path]
            ts, vs = self._sample_buffers[path]
            curve.setData(ts, vs)

        # 从 SamplingStatus 中获取实际采样率
        self._actual_sample_rate = getattr(self, '_actual_sample_rate', 0)

    def _on_sampling_status(self, event: SamplingStatus):
        self._sampling_active = event.is_running
        self._mcu_paused = event.paused

        if event.is_running:
            # 只对初次采样（无现有曲线时）才创建曲线
            if not self._plot_curves:
                self._plot_view.clear()
                legend = self._plot_view.addLegend()
                if legend:
                    legend.setBrush(pg.mkBrush(30, 30, 45, 200))
                    legend.setPen(pg.mkPen(color=(80, 80, 100), width=1))
                for i, name in enumerate(sorted(self._monitored_vars)):
                    color = COLORS[i % len(COLORS)]
                    curve = self._plot_view.plot(
                        [], [],
                        pen=pg.mkPen(color=color, width=1.5),
                        name=name,
                        connect='finite',
                        autoDownsample=True,
                        clipToView=True,
                    )
                    self._plot_curves[name] = curve

            self._sampling_btn.setText("停止采样")
            self._sampling_btn.setObjectName("stopBtn")
            self._mcu_btn.setEnabled(True)
            self._actual_sample_rate = event.actual_rate

            # 根据 paused 状态更新 MCU 按钮并控制滚动
            if event.paused:
                self._mcu_btn.setText("恢复运行")
                self._mcu_btn.setObjectName("mcuResumeBtn")
                self._auto_scroll = False
            else:
                self._mcu_btn.setText("暂停运行")
                self._mcu_btn.setObjectName("mcuRunBtn")
                self._auto_scroll = True
        else:
            # 有缓冲区数据 → 可恢复采样；无数据 → 全新开始
            if self._sample_buffers:
                self._sampling_btn.setText("恢复采样")
                self._sampling_btn.setObjectName("resumeBtn")
            else:
                self._sampling_btn.setText("开始采样")
                self._sampling_btn.setObjectName("startBtn")
            self._mcu_btn.setEnabled(False)
            self._mcu_btn.setText("暂停运行")
            self._mcu_btn.setObjectName("mcuRunBtn")

        self._sampling_btn.style().unpolish(self._sampling_btn)
        self._sampling_btn.style().polish(self._sampling_btn)
        self._mcu_btn.style().unpolish(self._mcu_btn)
        self._mcu_btn.style().polish(self._mcu_btn)

    # ═══════════════════════════════════════════════════════════════
    # 变量树
    # ═══════════════════════════════════════════════════════════════

    def _populate_tree(self):
        filter_text = self._filter_input.text().lower()
        self._var_tree.clear()
        self._tree_item_by_path.clear()

        from collections import defaultdict
        groups = defaultdict(list)
        for v in self._elf_variables:
            fn = v.file_name or ""
            if filter_text and filter_text not in v.name.lower():
                continue
            groups[fn].append(v)

        self._var_tree.blockSignals(True)

        # 确保有文件名的组排前面，无文件名的放最后
        keys = sorted(k for k in groups if k)
        if "" in groups:
            keys.append("")

        for fname in keys:
            items = sorted(groups[fname], key=lambda x: (x.address, x.name))
            if fname:
                folder = QTreeWidgetItem(self._var_tree)
                short = Path(fname).name
                folder.setText(0, f"📄  {short}  [{len(items)}]")
                folder.setFlags(
                    folder.flags() & ~Qt.ItemIsUserCheckable & ~Qt.ItemIsSelectable
                )
                font = folder.font(0)
                font.setBold(True)
                folder.setFont(0, font)
                folder.setForeground(0, QColor("#8090b0"))
                for v in items:
                    self._add_var_item(v, folder)
            else:
                for v in items:
                    self._add_var_item(v)

        self._var_tree.blockSignals(False)
        self._var_tree.collapseAll()
        self._recalculate_columns(2)
        self._update_stats()

    def _add_var_item(self, var_info, parent=None):
        from src.typedefs import (
            StructType, PointerType, ArrayType, TypedefType,
        )

        def resolve_type(ti):
            while isinstance(ti, TypedefType):
                ti = ti.underlying_type
            return ti

        concrete = resolve_type(var_info.type_info)
        path = var_info.name

        # 判断是否结构体（沿多级指针链走到尽头）
        struct = concrete if isinstance(concrete, StructType) else None
        if isinstance(concrete, PointerType) and struct is None:
            ti = concrete
            while isinstance(ti, PointerType) and ti.pointed_type:
                ti = ti.pointed_type
                while isinstance(ti, TypedefType):
                    ti = ti.underlying_type
                if isinstance(ti, StructType) and ti.members:
                    struct = ti
                    break

        # PointerType 变量：表达式加 * 前缀表示解引用
        is_ptr = isinstance(concrete, PointerType)
        if is_ptr:
            path = f"*{var_info.name}"

        is_array = isinstance(concrete, ArrayType) and concrete.count > 0
        MAX_ARRAY_ELEMS = 128

        item = QTreeWidgetItem() if parent is None else QTreeWidgetItem(parent)
        item.setText(0, var_info.name)
        item.setText(2, format_type(var_info.type_info))
        item.setTextAlignment(2, Qt.AlignLeft | Qt.AlignVCenter)
        item.setData(0, ROLE_PATH, path)
        item.setData(0, ROLE_ADDR, var_info.address)
        item.setData(0, ROLE_TYPE, var_info.type_info)

        # 填充初始值：只有基础值节点才显示数值
        if is_base_type(var_info.type_info):
            init_val = self._elf_values.get(path)
            item.setText(1, f"{init_val:.4g}" if init_val is not None else "")
        else:
            item.setText(1, "")

        # 数组本身不可勾选（无意义），只有元素可以勾选
        if is_array:
            item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        else:
            # 非数组：只有基础值节点才可勾选和编辑
            is_base = is_base_type(var_info.type_info)
            flags = Qt.ItemIsEnabled
            if is_base:
                flags |= Qt.ItemIsUserCheckable | Qt.ItemIsEditable
                item.setCheckState(
                    0, Qt.Checked if path in self._monitored_vars else Qt.Unchecked
                )
            item.setFlags(item.flags() | flags)

        self._var_registry[path] = (var_info.address, var_info.type_info)
        self._tree_item_by_path[path] = item

        if parent is None:
            self._var_tree.addTopLevelItem(item)

        # 展开结构体成员（递归）
        if struct and struct.members and var_info.type_info:
            self._expand_struct(struct, item, path, var_info.address)

        # 展开数组元素
        if is_array:
            elem_type = concrete.element_type
            elem_size = concrete.total_size // concrete.count
            count = min(concrete.count, MAX_ARRAY_ELEMS)
            for i in range(count):
                elem_path = f"{path}[{i}]"
                elem_addr = var_info.address + i * elem_size
                child = QTreeWidgetItem(item)
                child.setText(0, f"{var_info.name}[{i}]")
                child.setText(2, format_type(elem_type))
                child.setTextAlignment(2, Qt.AlignLeft | Qt.AlignVCenter)
                e_is_base = is_base_type(elem_type)
                e_flags = Qt.ItemIsEnabled
                if e_is_base:
                    e_flags |= Qt.ItemIsUserCheckable | Qt.ItemIsEditable
                    child.setCheckState(
                        0,
                        Qt.Checked
                        if elem_path in self._monitored_vars
                        else Qt.Unchecked,
                    )
                child.setFlags(child.flags() | e_flags)
                child.setData(0, ROLE_PATH, elem_path)
                child.setData(0, ROLE_ADDR, elem_addr)
                child.setData(0, ROLE_TYPE, elem_type)
                # 填充初始值：只有基础值节点才显示数值
                if e_is_base:
                    ev = self._elf_values.get(elem_path)
                    child.setText(1, f"{ev:.4g}" if ev is not None else "")
                else:
                    child.setText(1, "")
                self._var_registry[elem_path] = (elem_addr, elem_type)
                self._tree_item_by_path[elem_path] = child

                # 数组元素是结构体或指针→结构体：递归展开
                from src.typedefs import StructType as ST, TypedefType as TD, PointerType as PT
                _et = elem_type
                while isinstance(_et, TD):
                    _et = _et.underlying_type
                if isinstance(_et, ST) and _et.members:
                    self._expand_struct(_et, child, elem_path, elem_addr)
                elif isinstance(_et, PT):
                    pointed = _et.pointed_type
                    while isinstance(pointed, TD):
                        pointed = pointed.underlying_type
                    if isinstance(pointed, ST) and pointed.members:
                        # 数组元素是指向结构体的指针 → 解引用展开
                        deref_path = f"*{elem_path}"
                        self._expand_struct(pointed, child, deref_path, elem_addr)

            if concrete.count > MAX_ARRAY_ELEMS:
                more_item = QTreeWidgetItem(item)
                more_item.setText(
                    0,
                    f"... 还有 {concrete.count - MAX_ARRAY_ELEMS} 个元素",
                )
                more_item.setFlags(
                    more_item.flags()
                    & ~Qt.ItemIsUserCheckable
                    & ~Qt.ItemIsSelectable
                )
                more_item.setForeground(0, QColor("#606080"))

    def _expand_struct(self, struct_type, parent_item, parent_path, base_addr, depth=0, max_depth=8):
        """递归展开结构体成员到树中。支持嵌套结构体。"""
        from src.typedefs import StructType, PointerType, TypedefType

        if depth > max_depth:
            return

        for member in sorted(struct_type.members, key=lambda x: x.offset):
            member_path = f"{parent_path}.{member.name}"
            member_addr = base_addr + member.offset

            child = QTreeWidgetItem(parent_item)
            child.setText(0, member.name)
            child.setText(2, format_type(member.type_info))
            child.setTextAlignment(2, Qt.AlignLeft | Qt.AlignVCenter)

            m_is_base = is_base_type(member.type_info)
            m_flags = Qt.ItemIsEnabled
            if m_is_base:
                m_flags |= Qt.ItemIsUserCheckable | Qt.ItemIsEditable
                child.setCheckState(
                    0,
                    Qt.Checked
                    if member_path in self._monitored_vars
                    else Qt.Unchecked,
                )
            child.setFlags(child.flags() | m_flags)
            child.setData(0, ROLE_PATH, member_path)
            child.setData(0, ROLE_ADDR, member_addr)
            child.setData(0, ROLE_TYPE, member.type_info)

            if m_is_base:
                mv = self._elf_values.get(member_path)
                child.setText(1, f"{mv:.4g}" if mv is not None else "")
            else:
                child.setText(1, "")

            self._var_registry[member_path] = (member_addr, member.type_info)
            self._tree_item_by_path[member_path] = child

            # 递归展开嵌套结构体/联合体（包括指针指向的结构体）
            m_ti = member.type_info
            while isinstance(m_ti, TypedefType):
                m_ti = m_ti.underlying_type

            # 直接嵌套结构体
            if isinstance(m_ti, StructType) and m_ti.members:
                self._expand_struct(m_ti, child, member_path, member_addr, depth + 1, max_depth)

            # 指针（含多级）指向的结构体
            if isinstance(m_ti, PointerType):
                # 沿指针链走到尽头
                pointed = m_ti.pointed_type
                while pointed:
                    while isinstance(pointed, TypedefType):
                        pointed = pointed.underlying_type
                    if isinstance(pointed, StructType) and pointed.members:
                        deref_path = f"{parent_path}.*{member.name}"
                        self._expand_struct(pointed, child, deref_path, member_addr, depth + 1, max_depth)
                        break
                    if not isinstance(pointed, PointerType):
                        break
                    pointed = pointed.pointed_type

            # 数组元素展开
            from src.typedefs import ArrayType as ArrTy
            if isinstance(m_ti, ArrTy) and m_ti.count > 0:
                elem_ti = m_ti.element_type
                elem_size = m_ti.total_size // m_ti.count
                for i in range(min(m_ti.count, 128)):
                    elem_path = f"{member_path}[{i}]"
                    elem_addr = member_addr + i * elem_size
                    ec = QTreeWidgetItem(child)
                    ec.setText(0, f"{member.name}[{i}]")
                    ec.setText(2, format_type(elem_ti))
                    ec.setTextAlignment(2, Qt.AlignLeft | Qt.AlignVCenter)
                    e_is_base = is_base_type(elem_ti)
                    e_flags = Qt.ItemIsEnabled
                    if e_is_base:
                        e_flags |= Qt.ItemIsUserCheckable | Qt.ItemIsEditable
                        ec.setCheckState(0, Qt.Checked if elem_path in self._monitored_vars else Qt.Unchecked)
                    ec.setFlags(ec.flags() | e_flags)
                    ec.setData(0, ROLE_PATH, elem_path)
                    ec.setData(0, ROLE_ADDR, elem_addr)
                    ec.setData(0, ROLE_TYPE, elem_ti)
                    if e_is_base:
                        ev = self._elf_values.get(elem_path)
                        ec.setText(1, f"{ev:.4g}" if ev is not None else "")
                    else:
                        ec.setText(1, "")
                    self._var_registry[elem_path] = (elem_addr, elem_ti)
                    self._tree_item_by_path[elem_path] = ec

    def _on_filter(self):
        self._populate_tree()

    def _recalculate_columns(self, moved_idx):
        """根据拖动的线重算对面列的宽度。"""
        avail = self._var_tree.viewport().width()

        if moved_idx == 0:
            # 拖 Line A（col0↔col1）：col2 不动，重算 col1
            new_col1 = max(40, avail - self._col_name_width - self._col_type_width - 6)
            self._var_tree.setColumnWidth(1, new_col1)
            self._col_value_width = new_col1

        elif moved_idx == 1:
            # 拖 Line B（col1↔col2）：col0 不动，重算 col2
            new_col2 = max(40, avail - self._col_name_width - self._col_value_width - 6)
            self._var_tree.setColumnWidth(2, new_col2)
            self._col_type_width = new_col2

        elif moved_idx == 2:
            # 拖 col2 右边框：col1 不动，重算 col0
            new_col0 = max(80, avail - self._col_value_width - self._col_type_width - 6)
            self._var_tree.setColumnWidth(0, new_col0)
            self._col_name_width = new_col0

        self._tree_resize_pending = False

    def _resize_tree_proportionally(self):
        """容器缩放时：三列按当前比例等比例缩放。"""
        avail = self._var_tree.viewport().width()
        total_stored = (
            self._col_name_width
            + self._col_value_width
            + self._col_type_width
        )
        if total_stored <= 0:
            return

        usable = avail - 6
        new_name = max(80, int(usable * self._col_name_width / total_stored))
        new_value = max(40, int(usable * self._col_value_width / total_stored))
        new_type = max(40, int(usable * self._col_type_width / total_stored))

        # 舍入误差补到变量名列
        total_new = new_name + new_value + new_type
        new_name += usable - total_new

        self._var_tree.setColumnWidth(0, new_name)
        self._var_tree.setColumnWidth(1, new_value)
        self._var_tree.setColumnWidth(2, new_type)
        self._col_name_width = new_name
        self._col_value_width = new_value
        self._col_type_width = new_type

    def _on_item_changed(self, item, column):
        """复选框或数值变更时同步状态。"""
        path = item.data(0, ROLE_PATH)
        if path is None:
            return

        if column == 0:
            # 复选框变更 → 立即发送事件
            checked = item.checkState(0) == Qt.Checked
            if checked:
                self._monitored_vars.add(path)
                self._visible_vars.add(path)
            else:
                self._monitored_vars.discard(path)
                self._visible_vars.discard(path)
                # 采样中取消勾选：在缓冲区末尾插入 NaN 断点
                if self._sampling_active and path in self._sample_buffers:
                    ts, vals = self._sample_buffers[path]
                    if ts:
                        ts.append(ts[-1])
                        vals.append(float('nan'))
            self._stat_selected_vars.setText(
                f"✅  已选: {len(self._monitored_vars)}"
            )
            self._publish(
                SelectVariableRequest(
                    expression=path, selected=checked
                )
            )

        elif column == 1:
            # 数值列被用户双击编辑
            if self._updating_tree_values:
                return  # 程序更新，忽略
            try:
                new_value = float(item.text(1))
            except ValueError:
                return
            self._publish(
                VariableWriteRequest(
                    expression=path, value=new_value
                )
            )

    def _on_select_all(self):
        """全选所有可勾选的变量。"""
        self._var_tree.blockSignals(True)
        root = self._var_tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._set_children_checked(root.child(i), Qt.Checked)
        self._var_tree.blockSignals(False)
        self._sync_monitored_from_tree()

    def _on_deselect_all(self):
        """取消全选。"""
        self._var_tree.blockSignals(True)
        root = self._var_tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._set_children_checked(root.child(i), Qt.Unchecked)
        self._var_tree.blockSignals(False)
        self._sync_monitored_from_tree()

    @staticmethod
    def _set_children_checked(item, state):
        """递归设置 item 及其所有子项的复选框状态。"""
        if item.flags() & Qt.ItemIsUserCheckable:
            item.setCheckState(0, state)
        for i in range(item.childCount()):
            ScopeMainWindow._set_children_checked(item.child(i), state)

    def _sync_monitored_from_tree(self):
        """从树中所有复选框状态重新构建 self._monitored_vars。"""
        self._monitored_vars.clear()
        self._visible_vars.clear()
        root = self._var_tree.invisibleRootItem()
        for i in range(root.childCount()):
            self._collect_checked(root.child(i))
        self._stat_selected_vars.setText(
            f"✅  已选: {len(self._monitored_vars)}"
        )

    def _collect_checked(self, item):
        if (
            item.flags() & Qt.ItemIsUserCheckable
            and item.checkState(0) == Qt.Checked
        ):
            path = item.data(0, ROLE_PATH)
            if path:
                self._monitored_vars.add(path)
                self._visible_vars.add(path)
        for i in range(item.childCount()):
            self._collect_checked(item.child(i))

    def _update_stats(self):
        if not self._elf_variables:
            self._empty_state_label.setVisible(True)
            self._var_tree.setVisible(False)
            return
        self._empty_state_label.setVisible(False)
        self._var_tree.setVisible(True)

        files = len(
            set(v.file_name for v in self._elf_variables if v.file_name)
        )
        self._stat_total_vars.setText(
            f"📊  变量: {len(self._elf_variables)}"
        )
        self._stat_total_files.setText(f"📁  文件: {files}")
        self._stat_selected_vars.setText(
            f"✅  已选: {len(self._monitored_vars)}"
        )

    # ═══════════════════════════════════════════════════════════════
    # 绘图
    # ═══════════════════════════════════════════════════════════════

    def _update_plot(self):
        """定时器回调：仅处理可视性、时间轴滚动和状态栏。
        曲线数据由 _on_sample_data 在收到每个样本时立即更新。
        """
        if not self._sample_buffers:
            return

        time_window = self._time_window_spin.value()
        latest_timestamp = 0.0

        for name, curve in self._plot_curves.items():
            visible = name in self._visible_vars
            curve.setVisible(visible)
            # 不再在此处调用 curve.setData()——已在 _on_sample_data 中即时完成
            if visible and name in self._sample_buffers:
                timestamps = self._sample_buffers[name][0]
                if timestamps:
                    if timestamps[-1] > latest_timestamp:
                        latest_timestamp = timestamps[-1]

        if latest_timestamp > 0 and self._auto_scroll:
            self._plot_view.setXRange(
                max(0, latest_timestamp - time_window),
                latest_timestamp,
                padding=0.02,
            )

        scroll_mark = "" if self._auto_scroll else " (暂停)"
        mcu_mark = " MCU暂停" if self._mcu_paused else ""
        self._status_rate.setText(
            f"  |  采样: {self._actual_sample_rate:.0f} Hz"
            f"  |  显示: {self._frame_rate} FPS"
            f"  |  窗口: {time_window}s{scroll_mark}{mcu_mark}"
        )

        self._update_tree_values(self._sample_buffers)

    def _update_tree_values(self, buffers: dict):
        """把采样数据的最新值更新到变量树的「数值」列。"""
        # 用户正在编辑时跳过，避免覆盖输入
        if self._var_tree.state() == QAbstractItemView.EditingState:
            return
        self._updating_tree_values = True
        for path in sorted(buffers.keys()):
            item = self._tree_item_by_path.get(path)
            if item is None:
                continue
            timestamps, values = buffers[path]
            value_text = f"{values[-1]:.4g}" if len(values) > 0 else "—"
            item.setText(1, value_text)
        self._updating_tree_values = False

    def _on_user_interact(self, view_box):
        if self._y_auto_btn.isChecked():
            self._y_auto_btn.setChecked(False)
        self._auto_scroll = False

    def _on_y_toggled(self, checked):
        if checked:
            self._plot_view.enableAutoRange(y=True)
            self._auto_scroll = True
            self._y_auto_btn.setText("Y自动")
        else:
            self._plot_view.enableAutoRange(y=False)
            self._y_auto_btn.setText("Y手动")

    def _set_frame_rate(self, fps):
        self._frame_rate = fps
        self._plot_timer.setInterval(max(8, int(1000 / fps)))
        for menu_action in self.menuBar().actions():
            menu = menu_action.menu()
            if menu and menu.title() == "显示(&D)":
                for action in menu.actions():
                    if action.data() == fps:
                        action.setChecked(True)

    # ═══════════════════════════════════════════════════════════════
    # 关闭
    # ═══════════════════════════════════════════════════════════════

    def closeEvent(self, event):
        self._publish(CloseApplication())
        event.accept()