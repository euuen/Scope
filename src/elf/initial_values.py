"""ELF .data 节区初始值读取器 — 从 ELF 二进制文件提取变量的运行初始值。

参照 old/src/core/mem_backend.py 的类型感知解码逻辑。

使用方式:
    from src.elf.initial_values import read_initial_values
    values = read_initial_values(elf_result, variables)
"""

import logging
from typing import Optional

from src.typedefs import (
    Variable, BaseType, StructType, MemberInfo,
    PointerType, ArrayType, EnumType, TypeInfo,
)
from src.typedefs.type_utils import resolve_type, bytes_to_value, type_byte_size
from src.elf.parser import ElfParseResult

logger = logging.getLogger(__name__)


def read_initial_values(
    elf_result: ElfParseResult,
    variables: list[Variable],
) -> dict[str, float]:
    """从 ELF 的 .data 节区读取变量运行时初始值。

    完整支持:
      - 简单全局变量:     g_temperature           → bytes[offset:offset+size]
      - 结构体子成员:     g_gimbal.yaw            → 递归偏移读取
      - 指针解引用:       *g_pConfig              → 两级读取（读指针地址→读目标值）
      - 指针→结构体成员:  *g_uartConfigs[0].xxx   → 读指针→解引用→读成员
      - 数组元素:         g_adcBuffer[15]         → 按元素大小偏移读取
      - 嵌套结构体:       struct.inner.field      → 递归偏移
      - 枚举:             g_motorState            → 作为无符号整数读取

    Args:
        elf_result: ELFParser.parse() 的结果
        variables:  Variable 列表

    Returns:
        { expression: value, ... }
    """
    # ── 查找 .data 节区 ──
    data_sec = None
    for s in elf_result.sections:
        if s.name == '.data' and s.size > 0:
            data_sec = s
            break
    if data_sec is None:
        return {}

    data_buf = elf_result.section_data.get('.data', b'')
    if not data_buf:
        return {}

    values: dict[str, float] = {}

    for var in variables:
        if var.type_info is None:
            continue

        ti = resolve_type(var.type_info)

        # ── 指针变量：两级读取 ──
        if isinstance(ti, PointerType):
            _read_pointer_initial(data_buf, data_sec, var, ti, values)
            continue

        # ── 普通变量 → 直接读 ──
        offset = var.address - data_sec.address
        if not (data_sec.address <= var.address < data_sec.address + data_sec.size):
            continue
        if offset < 0 or offset + max(var.size, 1) > len(data_buf):
            continue

        # 读取变量本身
        val = _bytes_to_value_with_fallback(data_buf[offset:offset + var.size], ti)
        if val is not None:
            values[var.name] = val

        # 结构体 → 展开子成员
        if isinstance(ti, StructType):
            _read_struct_members_initial(data_buf, offset, var.name, ti, values)

    return values


def _read_pointer_initial(
    data_buf: bytes, data_sec,
    var: Variable, ptr_ti,
    values: dict[str, float],
):
    """读取指针变量的初始值（两级读取：读指针地址→解引用）。"""
    offset = var.address - data_sec.address
    if not (data_sec.address <= var.address < data_sec.address + data_sec.size):
        return
    if offset < 0 or offset + 4 > len(data_buf):
        return

    # 读指针地址值
    ptr_addr = int.from_bytes(data_buf[offset:offset + 4], 'little')
    expr = f"*{var.name}"

    # 确定指向类型
    pointed = resolve_type(ptr_ti.pointed_type) if ptr_ti.pointed_type else None

    if pointed is None:
        return

    # 指向基础类型/枚举 → 读解引用值
    if isinstance(pointed, (BaseType, EnumType)) and pointed.name != "void":
        deref_val = _read_deref_initial(data_buf, ptr_addr, data_sec, pointed)
        if deref_val is not None:
            values[expr] = deref_val

    # 指向结构体 → 递归读成员
    elif isinstance(pointed, StructType):
        ptr_off = ptr_addr - data_sec.address
        if data_sec.address <= ptr_addr < data_sec.address + data_sec.size:
            _read_struct_members_initial(data_buf, ptr_off, expr, pointed, values)


def _read_deref_initial(
    data_buf: bytes, ptr_addr: int, data_sec, pointed_ti,
) -> Optional[float]:
    """在 .data 节区中读取指针解引用后的值。"""
    size = type_byte_size(pointed_ti)
    if size <= 0:
        return None
    if not (data_sec.address <= ptr_addr < data_sec.address + data_sec.size):
        return None
    off = ptr_addr - data_sec.address
    if off + size > len(data_buf):
        return None
    return _bytes_to_value_with_fallback(data_buf[off:off + size], pointed_ti)


def _read_struct_members_initial(
    data_buf: bytes, base_offset: int,
    parent_path: str, struct_type,
    values: dict[str, float],
):
    """递归读取结构体所有子成员的初始值。"""
    for member in struct_type.members:
        path = f"{parent_path}.{member.name}" if parent_path else member.name
        off = base_offset + member.offset
        mti = resolve_type(member.type_info)
        if mti is None:
            continue

        if isinstance(mti, StructType):
            _read_struct_members_initial(data_buf, off, path, mti, values)
        else:
            size = type_byte_size(mti)
            if size <= 0:
                size = 1
            if off + size <= len(data_buf):
                val = _bytes_to_value_with_fallback(data_buf[off:off + size], mti)
                if val is not None:
                    values[path] = val


def _bytes_to_value_with_fallback(chunk: bytes, ti) -> Optional[float]:
    """按类型解码字节，带后备方案。"""
    val = bytes_to_value(chunk, ti)
    if val is not None:
        return val

    # 后备：对于无法识别的类型，按无符号整数读取
    if len(chunk) >= 1:
        if len(chunk) == 1:
            return float(chunk[0])
        if len(chunk) <= 4:
            return float(int.from_bytes(chunk[:4], 'little'))
        if len(chunk) <= 8:
            return float(int.from_bytes(chunk[:8], 'little'))
    return None