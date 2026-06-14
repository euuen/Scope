"""内存读取计划 —— 预计算类型感知的内存读取参数。

参照 old/src/core/mem_backend.py _TypeDecoder.make_plan() + _extract_val()。

使用方式:
    plan = make_read_plan(0x20000004, BaseType("float", 4))
    raw = ap.read_memory(plan.word_addr, transfer_size=32)  # 硬件读取
    value = extract_value(raw, plan)                          # 解码
"""

import struct

from src.typedefs.type_utils import resolve_type


class ReadPlan:
    """读取计划 — 预计算好的内存读取参数。

    属性:
        word_addr:    32-bit 字对齐地址（从哪个字读）
        byte_offset:  在该字内的字节偏移
        width:        要读取的字节数 (1/2/4/8)
        word_count:   跨越的 32-bit 字数
        is_signed:    是否有符号（用于符号扩展）
        is_float:     是否是浮点类型（用于 reinterpret）
    """

    __slots__ = ('word_addr', 'byte_offset', 'width',
                 'word_count', 'is_signed', 'is_float')

    def __init__(self, word_addr: int, byte_offset: int,
                 width: int, is_signed: bool, is_float: bool):
        self.word_addr = word_addr
        self.byte_offset = byte_offset
        self.width = width
        self.word_count = (byte_offset + width + 3) // 4
        self.is_signed = is_signed
        self.is_float = is_float

    def __repr__(self) -> str:
        return (f"ReadPlan(word_addr=0x{self.word_addr:X}, "
                f"byte_offset={self.byte_offset}, width={self.width}, "
                f"word_count={self.word_count}, "
                f"is_signed={self.is_signed}, is_float={self.is_float})")


def make_read_plan(addr: int, ti) -> ReadPlan:
    """从地址和类型信息创建读取计划 — 对应 old mem_backend.make_plan()。

    Args:
        addr: 变量的物理地址
        ti:   变量的类型信息

    Returns:
        ReadPlan 对象
    """
    ti = resolve_type(ti)

    from src.typedefs import BaseType, PointerType, EnumType

    # 默认宽度
    width = 4
    is_float = False
    is_signed = False

    if isinstance(ti, BaseType):
        width = max(ti.byte_size, 1)
        if width > 8:
            width = 8  # 安全性上限 64-bit
        encoding = getattr(ti, 'encoding', '')
        name_lower = ti.name.lower()
        is_float = 'float' in encoding or 'float' in name_lower or 'double' in name_lower
        is_signed = encoding.startswith('signed') or ('int' in name_lower and 'uint' not in name_lower)

    elif isinstance(ti, PointerType):
        width = ti.size if ti.size else 4

    elif isinstance(ti, EnumType):
        width = ti.size if ti.size else 4

    # 对齐到 32-bit 字
    word_addr = addr & ~0x3
    byte_offset = addr & 0x3

    return ReadPlan(word_addr, byte_offset, width, is_signed, is_float)


def extract_value(words, plan: ReadPlan) -> float:
    """从读取的 32-bit 字中提取变量值 — 对应 old mem_backend._extract_val()。

    Args:
        words: 单个 int（单字）或 list[int]（多字跨字边界）
        plan:  读取计划

    Returns:
        解码后的浮点数值
    """
    if isinstance(words, int):
        raw = (words >> (plan.byte_offset * 8)) & ((1 << (plan.width * 8)) - 1)
    else:
        # 多字: 组合 word_count 个 32-bit 字为一个大整数
        val = 0
        for k in range(plan.word_count):
            w = words[k] if k < len(words) else 0
            val |= (w & 0xFFFFFFFF) << (k * 32)
        raw = (val >> (plan.byte_offset * 8)) & ((1 << (plan.width * 8)) - 1)

    # float reinterpret
    if plan.is_float and plan.width == 4:
        return struct.unpack('<f', struct.pack('<I', raw & 0xFFFFFFFF))[0]

    # 有符号扩展
    if plan.is_signed:
        if plan.width == 1:
            return float(raw - 256 if raw >= 128 else raw)
        if plan.width == 2:
            return float(raw - 65536 if raw >= 32768 else raw)
        if plan.width == 4:
            return float(raw - 4294967296 if raw >= 2147483648 else raw)
        if plan.width == 8:
            return float(raw - (1 << 63) if raw >= (1 << 63) else raw)

    return float(raw)


def encode_value(value: float, ti) -> int:
    """按类型信息编码浮点值为原始 32-bit 字。

    用于变量写入操作。

    Args:
        value: 要编码的浮点值
        ti:    类型信息

    Returns:
        编码后的 32-bit 整数
    """
    ti = resolve_type(ti)

    from src.typedefs import BaseType

    if isinstance(ti, BaseType):
        name = ti.name.lower()
        encoding = getattr(ti, 'encoding', '')
        if 'float' in encoding or 'float' in name or 'double' in name:
            return struct.unpack('<I', struct.pack('<f', value))[0]

    return int(value)


def extract_ptr_value(raw: int, plan: ReadPlan) -> int:
    """从读取的 32-bit 字中提取指针值（地址）。

    Args:
        raw:  从 MEM-AP 读取的原始 32-bit 值
        plan: 读取计划

    Returns:
        指针指向的目标地址
    """
    return (raw >> (plan.byte_offset * 8)) & 0xFFFFFFFF