"""Scope 探针模块 —— 基于 pyOCD 的调试探针扫描、连接和数据读写。"""

from .probe_node import ProbeNode
from .scanner import ProbeInfo, scan_probes
from .connection import ConnectionHandle, connect_probe, disconnect_probe
from .read_plan import ReadPlan, make_read_plan, extract_value, encode_value, extract_ptr_value

__all__ = [
    "ProbeNode",
    "ProbeInfo", "scan_probes",
    "ConnectionHandle", "connect_probe", "disconnect_probe",
    "ReadPlan", "make_read_plan", "extract_value", "encode_value", "extract_ptr_value",
]