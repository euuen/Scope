"""探针连接管理器 —— 基于 pyOCD 建立/断开与调试探针的连接。"""

import logging
from dataclasses import dataclass
from typing import Optional

from .scanner import ProbeInfo

logger = logging.getLogger(__name__)


@dataclass
class ConnectionHandle:
    target_name: str
    probe_name: str
    swd_freq_khz: int
    session: object = None


def connect_probe(probe: ProbeInfo, swd_freq_hz: int = 4_000_000,
                  mode: str = "attach",
                  target_override: str = "") -> ConnectionHandle:
    """连接到指定探针。

    Args:
        probe: 目标探针信息
        swd_freq_hz: SWD 频率 (Hz)
        mode: "attach" 或 "reset"（连接后复位目标）
        target_override: 强制指定目标芯片型号，如 "stm32g0b1re"。留空则由 pyOCD 自动识别。

    Returns:
        ConnectionHandle

    Raises:
        ConnectionError: 连接失败
    """
    try:
        from pyocd.probe.aggregator import DebugProbeAggregator
        from pyocd.core.session import Session
    except ImportError:
        raise ConnectionError("pyOCD 未安装")

    try:
        all_probes = DebugProbeAggregator.get_all_connected_probes()
        target_probe = None
        for p in all_probes:
            if p.unique_id == probe.uid:
                target_probe = p
                break
        if target_probe is None:
            raise ConnectionError(f"未找到探针 {probe.name} (UID: {probe.uid})")

        opts: dict = {
            "frequency": swd_freq_hz,
            "connect_mode": "attach",
        }
        # 用户指定了目标型号 → 传给 pyOCD 避免 auto-detect 失败
        if target_override:
            opts["target_override"] = target_override

        session = Session(probe=target_probe, options=opts)
        session.open()
        target = session.target

        # 校验 MEM-AP 是否存在（参考 old mem_backend.py）
        if not target or not target.aps:
            session.close()
            raise ConnectionError("未找到 MEM-AP（AHB-AP），请检查 SWD 接线和芯片供电")

        # reset + resume 模式
        if mode == "reset" and target:
            try:
                target.reset_and_halt()
                target.resume()
            except Exception as e:
                logger.warning(f"复位后启动失败: {e}")

        target_name = target.part_number or target_override or probe.target or "cortex_m"

        logger.info(f"已连接 {probe.name} → {target_name} ({swd_freq_hz // 1000} kHz)")
        return ConnectionHandle(
            target_name=target_name,
            probe_name=probe.name,
            swd_freq_khz=swd_freq_hz // 1000,
            session=session,
        )

    except ConnectionError:
        raise
    except Exception as e:
        raise ConnectionError(f"pyOCD 连接失败: {e}") from e


def disconnect_probe(handle: Optional[ConnectionHandle]) -> None:
    """断开探针连接。"""
    if handle is None:
        return
    if handle.session:
        try:
            handle.session.close()
        except Exception:
            pass
    logger.info("已断开")