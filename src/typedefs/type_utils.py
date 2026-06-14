"""类型工具函数 —— 穿透 TypedefType、判断是否为基础可读类型、从字节按类型解码值。"""

import struct
from typing import Optional

from src.typedefs import (
    BaseType, StructType, ArrayType, PointerType, EnumType, TypedefType, TypeInfo,
)


def resolve_type(ti) -> TypeInfo:
    """穿透 TypedefType 链，返回实体类型。"""
    while isinstance(ti, TypedefType):
        ti = ti.underlying_type
    return ti


def is_base_like(ti) -> bool:
    """判断类型是否为"基础可读类型"。

    基础可读类型 = 可以通过一次内存读取得到数值的类型。
    非基础可读类型（结构体/数组/函数）需要展开成子成员才能读取。

    规则：
      - BaseType → 是（void 除外）
      - EnumType → 是
      - PointerType → 若指向的底层为基础类型或枚举 → 是
      - StructType / ArrayType / FuncType / None → 否
      - TypedefType → 解析后递归判断
    """
    ti = resolve_type(ti)

    if isinstance(ti, BaseType):
        return ti.name != "void"

    if isinstance(ti, EnumType):
        return True

    if isinstance(ti, PointerType):
        pointed = resolve_type(ti.pointed_type) if ti.pointed_type else None
        if pointed is None:
            return False
        if isinstance(pointed, BaseType):
            return pointed.name != "void"
        if isinstance(pointed, EnumType):
            return True
        # 指向结构体 → 不可直接读（需要展开子成员）
        return False

    return False


def type_byte_size(ti) -> int:
    """获取类型的字节大小。

    支持所有类型，包括 TypedefType 穿透。
    """
    ti = resolve_type(ti)
    if ti is None:
        return 0
    if isinstance(ti, BaseType):
        return ti.byte_size
    if isinstance(ti, StructType):
        return ti.size
    if isinstance(ti, ArrayType):
        return ti.total_size
    if isinstance(ti, PointerType):
        return ti.size if ti.size else 4
    if isinstance(ti, EnumType):
        return ti.size if ti.size else 4
    return 0


def bytes_to_value(chunk: bytes, ti) -> Optional[float]:
    """按类型信息从字节数据中解码数值。

    参照 old/src/core/mem_backend.py _decode_raw() 和 _extract_val()。

    支持:
      - BaseType: unsigned / signed / float / double（1/2/4/8 字节）
      - TypedefType: 穿透后递归
      - PointerType: 作为 uint32/uint64 读取
      - EnumType: 作为无符号整数读取

    Args:
        chunk: 原始字节数据（长度必须 >= 类型大小）
        ti:    类型信息

    Returns:
        解码后的浮点值，或 None（无法解码）
    """
    ti = resolve_type(ti)
    if ti is None or not chunk:
        return None

    if isinstance(ti, BaseType):
        return _decode_base_bytes(chunk, ti)

    if isinstance(ti, PointerType):
        # 指针值：作为无符号整数读取
        size = ti.size if ti.size else 4
        return _decode_unsigned(chunk, size)

    if isinstance(ti, EnumType):
        size = ti.size if ti.size else 4
        return _decode_unsigned(chunk, size)

    if isinstance(ti, StructType):
        # 结构体本身不可直接解码为数值，返回 None
        return None

    if isinstance(ti, ArrayType):
        # 数组本身不可直接解码为数值，返回 None
        return None

    return None


def _decode_base_bytes(chunk: bytes, bt: BaseType) -> Optional[float]:
    """解码 BaseType 字节。"""
    size = bt.byte_size
    if size <= 0 or len(chunk) < size:
        return None

    name_lower = bt.name.lower()
    encoding = (bt.encoding or '').lower()

    # ── float / double ──
    if 'float' in encoding or 'float' in name_lower:
        if size == 4:
            return float(struct.unpack_from('<f', chunk, 0)[0])
        if size == 8:
            return float(struct.unpack_from('<d', chunk, 0)[0])
        return _decode_unsigned(chunk, size)

    if 'double' in encoding or 'double' in name_lower:
        if size == 8:
            return float(struct.unpack_from('<d', chunk, 0)[0])
        return _decode_unsigned(chunk, size)

    # ── signed ──
    is_signed = (
        encoding.startswith('signed')
        or ('int' in name_lower and 'uint' not in name_lower)
    )

    if is_signed:
        if size == 1:
            return float(struct.unpack_from('<b', chunk, 0)[0])
        if size == 2:
            return float(struct.unpack_from('<h', chunk, 0)[0])
        if size == 4:
            return float(struct.unpack_from('<i', chunk, 0)[0])
        if size == 8:
            return float(struct.unpack_from('<q', chunk, 0)[0])
        return _decode_unsigned(chunk, size)

    # ── unsigned（默认） ──
    return _decode_unsigned(chunk, size)


def _decode_unsigned(chunk: bytes, size: int) -> float:
    """按小端序解码无符号整数。"""
    if size == 1:
        return float(chunk[0])
    if size == 2:
        return float(struct.unpack_from('<H', chunk, 0)[0])
    if size == 4:
        return float(struct.unpack_from('<I', chunk, 0)[0])
    if size == 8:
        return float(struct.unpack_from('<Q', chunk, 0)[0])
    # 兜底
    val = 0
    for b in reversed(chunk[:size]):
        val = (val << 8) | b
    return float(val)