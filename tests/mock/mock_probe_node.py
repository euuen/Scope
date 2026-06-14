"""MockProbeNode —— 模拟探针扫描和连接的后端节点。

订阅: ScanProbesRequest, ConnectProbeRequest, DisconnectProbeRequest
发布: ProbeScanResult, ProbeConnected, ProbeDisconnected, ProbeConnectionFailed
"""

import logging
import random

from src.framework import Node
from src.scope import (
    ScanProbesRequest, ConnectProbeRequest, DisconnectProbeRequest,
    VariableWriteRequest,
    ProbeScanResult, ProbeConnected, ProbeDisconnected, ProbeConnectionFailed,
)

logger = logging.getLogger(__name__)


class MockProbeNode(Node):
    """模拟探针管理节点。

    模拟 pyOCD 探针的扫描、连接和断开过程，无需实际硬件。
    默认模拟成功，可通过设置 fail_next=True 触发失败场景。
    """

    def __init__(self, name: str = "MockProbeNode"):
        super().__init__(name)
        self._connected = False
        self._fail_next_scan = False
        self._fail_next_connect = False
        self._current_probe = None

    def _init(self):
        self.subscribe(ScanProbesRequest, self._on_scan)
        self.subscribe(ConnectProbeRequest, self._on_connect)
        self.subscribe(DisconnectProbeRequest, self._on_disconnect)
        self.subscribe(VariableWriteRequest, self._on_write_variable)

    def set_fail_scan(self, fail: bool = True):
        """设置下次扫描是否模拟失败。"""
        self._fail_next_scan = fail

    def set_fail_connect(self, fail: bool = True):
        """设置下次连接是否模拟失败。"""
        self._fail_next_connect = fail

    def _on_scan(self, event: ScanProbesRequest):
        if self._fail_next_scan:
            self._fail_next_scan = False
            self.publish(ProbeScanResult(probes=[]))
            logger.info("[MockProbeNode] 模拟扫描失败（无探针）")
            return

        # 模拟 3 个探针
        probes = [
            {"name": "DAPLink-CMSIS-DAP", "vendor": "ARM", "uid": "A1B2C3D4E5F6", "target": "stm32g0b1"},
            {"name": "ST-Link/V3", "vendor": "STMicroelectronics", "uid": "F0E1D2C3B4A5", "target": "stm32f407"},
            {"name": "JLink", "vendor": "SEGGER", "uid": "9876543210AB", "target": "nrf52840"},
        ]
        self.publish(ProbeScanResult(probes=probes))
        logger.info(f"[MockProbeNode] 扫描到 {len(probes)} 个探针")

    def _on_connect(self, event: ConnectProbeRequest):
        if self._connected:
            logger.info("[MockProbeNode] 已经连接")
            return

        if self._fail_next_connect:
            self._fail_next_connect = False
            self.publish(ProbeConnectionFailed(reason="模拟连接失败：未找到目标芯片"))
            logger.warning("[MockProbeNode] 模拟连接失败")
            return

        self._connected = True
        self._current_probe = {
            "index": event.probe_index,
            "mode": event.mode,
            "freq": event.swd_freq_hz,
            "target": f"stm32g0b1retx",
            "name": "DAPLink-CMSIS-DAP",
        }

        self.publish(ProbeConnected(
            target_name=self._current_probe["target"],
            swd_freq_khz=event.swd_freq_hz // 1000,
            probe_name=self._current_probe["name"],
        ))
        logger.info(f"[MockProbeNode] 已连接 → {self._current_probe['target']} "
                     f"(SWD: {event.swd_freq_hz // 1000} kHz)")

    def _on_disconnect(self, event: DisconnectProbeRequest):
        if not self._connected:
            return
        self._connected = False
        self._current_probe = None
        self.publish(ProbeDisconnected())
        logger.info("[MockProbeNode] 已断开连接")

    def _on_write_variable(self, event: VariableWriteRequest):
        """mock 写变量：只打日志，不操作实际硬件。"""
        logger.info(
            f"[MockProbeNode] 写入变量  {event.expression}  =  {event.value}"
        )