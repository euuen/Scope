"""ProbeNode —— 调试探针节点。

订阅: ScanProbesRequest, ConnectProbeRequest, DisconnectProbeRequest,
      VariableWriteRequest, StartSamplingRequest, StopSamplingRequest,
      ChangeSampleRateRequest, SelectVariableRequest, ElfLoaded
发布: ProbeScanResult, ProbeConnected, ProbeDisconnected,
      ProbeConnectionFailed, SampleData, SamplingStatus
"""

import asyncio
import logging
import re
import struct
import time
from typing import Optional

from src.framework import Node
from src.scope import (
    ScanProbesRequest, ConnectProbeRequest, DisconnectProbeRequest,
    VariableWriteRequest, StartSamplingRequest, StopSamplingRequest,
    ChangeSampleRateRequest, SelectVariableRequest,
    ProbeScanResult, ProbeConnected, ProbeDisconnected,
    ProbeConnectionFailed, SampleData, SamplingStatus,
)
from .scanner import scan_probes, ProbeInfo
from .connection import connect_probe, disconnect_probe, ConnectionHandle
from src.typedefs import BaseType, StructType, PointerType, ArrayType, EnumType
from src.typedefs.type_utils import resolve_type, type_byte_size

logger = logging.getLogger(__name__)


class ProbeNode(Node):
    """探针节点。

    ELF 加载时预展开所有表达式地址 → 运行时查表即得。
    指针解引用（*expr）需运行时两阶段读，其他全部 O(1) 查表。
    """

    def __init__(self, name: str = "ProbeNode"):
        super().__init__(name)
        self._handle: Optional[ConnectionHandle] = None
        self._session = None
        self._sampling = False
        self._expressions: list[str] = []
        self._selected: set[str] = set()
        self._sample_rate = 100
        self._sample_count = 0
        self._t0 = 0.0

        # { 表达式: (地址, 类型) }  ELF 加载时预展开
        self._expr_map: dict[str, tuple[int, object]] = {}
        self._last_probes: list[ProbeInfo] = []

    def _init(self):
        self.subscribe(ScanProbesRequest, self._on_scan)
        self.subscribe(ConnectProbeRequest, self._on_connect)
        self.subscribe(DisconnectProbeRequest, self._on_disconnect)
        self.subscribe(StartSamplingRequest, self._on_start_sampling)
        self.subscribe(StopSamplingRequest, self._on_stop_sampling)
        self.subscribe(ChangeSampleRateRequest, self._on_change_rate)
        self.subscribe(SelectVariableRequest, self._on_select)
        self.subscribe(VariableWriteRequest, self._on_write_variable)
        from src.scope import ElfLoaded
        self.subscribe(ElfLoaded, self._on_elf_loaded)

    # ══════════════════════════════════════════════════════════
    #  ELF 加载 → 预展开所有表达式地址
    # ══════════════════════════════════════════════════════════

    def _on_elf_loaded(self, event):
        """加载 ELF 时递归展开全部表达式路径。"""
        self._expr_map.clear()
        if not event.variables:
            return
        for v in event.variables:
            self._expand(v.name, v.address, v.type_info, 0)
        logger.info(f"ProbeNode: 预展开 {len(self._expr_map)} 个表达式")
        # 打印前 20 个注册条目供调试
        for k, (a, t) in list(self._expr_map.items())[:20]:
            tn = type(t).__name__ if t else "None"
            logger.info(f"  [{tn}] {k} @ 0x{a:X}")

    def _expand(self, prefix: str, addr: int, ti, depth: int):
        """递归展开一个变量及其所有子成员的地址。不注册 *expr 指针解引用条目，由运行时递归。"""
        if depth > 20:
            return
        ti = resolve_type(ti)
        if ti is None:
            return

        # 注册本节点
        self._expr_map[prefix] = (addr, ti)

        # 结构体成员
        if isinstance(ti, StructType) and ti.members:
            for m in ti.members:
                self._expand(f"{prefix}.{m.name}", addr + m.offset, m.type_info, depth + 1)
            return

        # 数组元素
        if isinstance(ti, ArrayType) and ti.count > 0:
            elem_size = ti.total_size // ti.count
            for i in range(min(ti.count, 256)):
                elem_addr = addr + i * elem_size
                self._expand(f"{prefix}[{i}]", elem_addr, ti.element_type, depth + 1)
            return

        # 指针变量: 不注册 *expr 条目, 运行时递归 resolve('*') 处理

    # ══════════════════════════════════════════════════════════
    #  表达式 → (地址, 类型)  运行时解析
    # ══════════════════════════════════════════════════════════

    def resolve(self, expr: str) -> tuple[int, object]:
        """表达式 → (内存地址, 类型)。全递归。

        *expr.member 中 * 只作用于 expr（指针部分），.member 在解引用后走。
        """
        # ── 1. 基例：查预展开表 ──
        entry = self._expr_map.get(expr)
        if entry:
            return entry

        # ── 2. 指针解引用 *expr 或 *expr.member ──
        if expr.startswith('*'):
            inner = expr[1:]
            # 拆出指针根和成员路径
            # *g_uartConfigs[0].baud_rate → ptr="g_uartConfigs[0]" member="baud_rate"
            # **g_ppConfig              → ptr="*g_ppConfig" member=""
            dot = inner.find('.')
            ptr_part = inner[:dot] if dot >= 0 else inner
            member_path = inner[dot + 1:] if dot >= 0 else ""

            # 递归解析指针变量
            ptr_addr, ptr_ti = self.resolve(ptr_part)

            # 读指针值
            ap = self._get_ap()
            target = 0
            if ap:
                try:
                    target = ap.read_memory(ptr_addr & ~0x3, transfer_size=32) & 0xFFFFFFFF
                except Exception:
                    pass

            # 从 ptr_ti 推导解引用后的类型
            pti = resolve_type(ptr_ti)
            pointed_ti = None
            if isinstance(pti, PointerType) and pti.pointed_type:
                pointed_ti = pti.pointed_type
            elif isinstance(pti, ArrayType):
                elem = resolve_type(pti.element_type)
                if isinstance(elem, PointerType) and elem.pointed_type:
                    pointed_ti = elem.pointed_type

            if pointed_ti is None:
                pointed_ti = BaseType("uint32_t", 4)

            if not member_path:
                return target, pointed_ti

            # 有成员：在解引用后的类型上走
            rpt = resolve_type(pointed_ti)
            if isinstance(rpt, (StructType, PointerType)):
                return self._walk(target, rpt, member_path)
            return target, BaseType("uint32_t", 4)

        # ── 3. 成员访问 a.b ──
        dot = expr.find('.')
        if dot >= 0:
            return self._walk(*self.resolve(expr[:dot]), expr[dot + 1:])

        # ── 4. 数组元素 name[i] ──
        m = re.match(r'^(.+)\[(\d+)\]$', expr)
        if m:
            addr, ti = self.resolve(m.group(1))
            ti = resolve_type(ti)
            idx = int(m.group(2))
            if isinstance(ti, ArrayType) and 0 <= idx < ti.count:
                es = ti.total_size // ti.count
                return (addr + idx * es, ti.element_type)
            raise KeyError(f"数组越界: {expr}")

        raise KeyError(f"未知: {expr}")

    def _walk(self, addr: int, ti, path: str) -> tuple[int, object]:
        """沿成员路径走。找不到成员时返回当前地址和 uint32。
        遇到指针或 *member 自动解引用。
        """
        for seg in path.split('.'):
            ti = resolve_type(ti)
            is_ptr_mark = seg.startswith('*')
            if is_ptr_mark:
                seg = seg[1:]

            # PointerType 自动解引用
            while isinstance(ti, PointerType):
                ap = self._get_ap()
                if ap:
                    try:
                        addr = ap.read_memory(addr & ~0x3, transfer_size=32) & 0xFFFFFFFF
                    except Exception:
                        pass
                ti = ti.pointed_type if ti.pointed_type else BaseType("uint32_t", 4)
                ti = resolve_type(ti)

            # 非结构体 → 无法走成员，返回当前地址
            if not isinstance(ti, StructType) or not ti.members:
                break

            # 找成员
            found = False
            for m in ti.members:
                if m.name == seg:
                    addr += m.offset
                    ti = m.type_info
                    # *member 显式解引用
                    if is_ptr_mark:
                        rti = resolve_type(ti)
                        if isinstance(rti, PointerType):
                            ap = self._get_ap()
                            if ap:
                                try:
                                    addr = ap.read_memory(addr & ~0x3, transfer_size=32) & 0xFFFFFFFF
                                except Exception:
                                    pass
                            ti = rti.pointed_type if rti.pointed_type else BaseType("uint32_t", 4)
                    found = True
                    break

            if not found:
                # 找不到成员 → 停止走路径，返回当前地址
                break

        return addr, ti

    # ══════════════════════════════════════════════════════════
    #  读内存
    # ══════════════════════════════════════════════════════════

    def _get_ap(self):
        if self._session is None:
            return None
        t = self._session.target
        return list(t.aps.values())[0] if t and t.aps else None

    def _read(self, addr: int, ti) -> float:
        """按类型从 addr 读一个值。处理 float/signed/unsigned 和字节偏移。"""
        ap = self._get_ap()
        if ap is None:
            return 0.0
        ti = resolve_type(ti)
        try:
            raw = ap.read_memory(addr & ~0x3, transfer_size=32)
        except Exception:
            return float('nan')

        off = addr & 0x3
        sz = type_byte_size(ti)
        if sz <= 0:
            sz = 4

        mask = (1 << (min(sz * 8, 32))) - 1
        val = (raw >> (off * 8)) & mask

        if isinstance(ti, BaseType):
            name_l = ti.name.lower()
            enc = (ti.encoding or '').lower()
            if 'float' in enc or 'float' in name_l:
                if sz == 4:
                    return struct.unpack('<f', struct.pack('<I', raw & 0xFFFFFFFF))[0]
                if sz == 8:
                    return float(struct.unpack('<d', struct.pack('<II', raw & 0xFFFFFFFF, 0))[0])
            if enc.startswith('signed') or ('int' in name_l and 'uint' not in name_l):
                if sz == 1:
                    return float(val - 256 if val >= 128 else val)
                if sz == 2:
                    return float(val - 65536 if val >= 32768 else val)
                if sz == 4:
                    return float(val - 4294967296 if val >= 2147483648 else val)

        return float(val)

    def read_variable(self, expr: str) -> Optional[float]:
        try:
            addr, ti = self.resolve(expr)
            return self._read(addr, ti)
        except KeyError:
            logger.warning(f"未知: {expr}")
            return None
        except Exception as e:
            logger.error(f"读 {expr}: {e}")
            return None

    def write_variable(self, expr: str, value: float) -> bool:
        ap = self._get_ap()
        if ap is None:
            return False
        try:
            addr, ti = self.resolve(expr)
        except KeyError:
            logger.warning(f"未知: {expr}")
            return False

        ti = resolve_type(ti)
        raw = int(value)
        if isinstance(ti, BaseType):
            if 'float' in (ti.encoding or '') or 'float' in ti.name.lower():
                raw = struct.unpack('<I', struct.pack('<f', value))[0]
        try:
            ap.write_memory(addr & ~0x3, raw, transfer_size=32)
            logger.info(f"写入 {expr} = {value} @ 0x{addr:X}")
            return True
        except Exception as e:
            logger.error(f"写 {expr}: {e}")
            return False

    # ══════════════════════════════════════════════════════════
    #  事件
    # ══════════════════════════════════════════════════════════

    def _on_scan(self, event):
        try:
            probes = scan_probes()
        except Exception as e:
            logger.error(f"探针扫描异常: {e}")
            self.publish(ProbeScanResult(probes=[]))
            return

        self._last_probes = probes
        self.publish(ProbeScanResult(probes=[
            dict(name=p.name, vendor=p.vendor, uid=p.uid,
                 target=p.target, board_name=p.board_name,
                 protocol=p.debug_protocol) for p in probes
        ]))

    def _on_connect(self, event):
        if self._handle:
            return
        probes = self._last_probes
        if event.probe_index >= len(probes):
            self.publish(ProbeConnectionFailed(reason="请先扫描"))
            return
        try:
            self._handle = connect_probe(
                probes[event.probe_index],
                swd_freq_hz=event.swd_freq_hz,
                mode=event.mode,
                target_override=event.target_override,
            )
            self._session = self._handle.session
            self.publish(ProbeConnected(
                target_name=self._handle.target_name,
                swd_freq_khz=self._handle.swd_freq_khz,
                probe_name=self._handle.probe_name,
            ))
        except Exception as e:
            self._handle = None; self._session = None
            self.publish(ProbeConnectionFailed(reason=str(e)))

    def _on_disconnect(self, event):
        if not self._handle:
            return
        disconnect_probe(self._handle)
        self._handle = None; self._session = None
        self._sampling = False
        self.publish(ProbeDisconnected())

    def _on_write_variable(self, event):
        if not self.write_variable(event.expression, event.value):
            logger.warning(f"写失败 {event.expression} = {event.value}")

    def _on_select(self, event):
        if event.selected:
            self._selected.add(event.expression)
            if self._sampling and event.expression not in self._expressions:
                self._expressions.append(event.expression)
        else:
            self._selected.discard(event.expression)
            if self._sampling and event.expression in self._expressions:
                self._expressions.remove(event.expression)

    def _on_start_sampling(self, event):
        if self._get_ap() is None:
            logger.warning("未连接")
            return
        if self._sampling:
            return
        self._expressions = sorted(self._selected)
        if not self._expressions:
            return
        self._sample_rate = event.sample_rate_hz or 100
        self._sample_count = 0
        self._t0 = 0.0
        self._sampling = True
        self.publish(SamplingStatus(is_running=True, sample_count=0, actual_rate=0.0))

    def _on_stop_sampling(self, event):
        if not self._sampling:
            return
        self._sampling = False
        self.publish(SamplingStatus(is_running=False, sample_count=self._sample_count, actual_rate=0.0))

    def _on_change_rate(self, event):
        self._sample_rate = event.sample_rate_hz

    async def _process(self):
        if not self._sampling:
            return
        ap = self._get_ap()
        if ap is None:
            return
        now = time.perf_counter()
        if self._t0 == 0.0:
            self._t0 = now
        if int((now - self._t0) * self._sample_rate) <= self._sample_count:
            return

        t = (self._sample_count + 1) / self._sample_rate
        sample = {}
        for expr in self._expressions:
            try:
                addr, ti = self.resolve(expr)
                sample[expr] = self._read(addr, ti)
            except KeyError:
                logger.warning(f"未知: {expr}")
            except Exception as e:
                logger.error(f"采样 {expr}: {e}")
        self._sample_count += 1
        if sample:
            self.publish(SampleData(buffers=sample, timestamps=[t]))
        await asyncio.sleep(0)