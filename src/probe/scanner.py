"""探针扫描器 —— 使用 pyOCD 扫描系统上可用的调试探针。"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ProbeInfo:
    """单个探针的信息。"""
    name: str
    vendor: str
    uid: str
    target: str = ""
    board_name: str = ""
    debug_protocol: str = "swd"


def scan_probes() -> list[ProbeInfo]:
    """扫描所有已连接的调试探针。

    使用 pyOCD 的 DebugProbeAggregator 进行扫描。
    返回 ProbeInfo 列表，未找到探针或无 pyOCD 时返回空列表。
    """
    try:
        from pyocd.probe.aggregator import DebugProbeAggregator
    except ImportError:
        logger.warning("pyOCD 未安装，无法扫描探针")
        return []

    probes: list[ProbeInfo] = []
    all_probes = DebugProbeAggregator.get_all_connected_probes()
    for p in all_probes:
        try:
            probes.append(ProbeInfo(
                name=p.product_name or p.vendor_name or "Unknown",
                vendor=p.vendor_name or "Unknown",
                uid=p.unique_id or "",
            ))
        except Exception as e:
            logger.warning(f"跳过探针 {getattr(p, 'unique_id', '?' )}: {e}")
            continue

    logger.info(f"扫描到 {len(probes)} 个探针 (共 {len(all_probes)} 个原始接口)")
    return probes