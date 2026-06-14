"""帮助函数与常量。"""

from PySide6.QtCore import Qt


# ── 常量 ────────────────────────────────────────────────────────────

COLORS = [
    "#00ff88", "#ff4488", "#44aaff", "#ffaa00", "#aa44ff",
    "#00ccff", "#ff6600", "#66ff44", "#ff44aa", "#44ffcc",
]

PRESET_FRAME_RATES = [12, 24, 30, 60, 120]
DEFAULT_FRAME_RATE = 60
DEFAULT_TIME_WINDOW = 10

ROLE_PATH = Qt.UserRole
ROLE_ADDR = Qt.UserRole + 1
ROLE_TYPE = Qt.UserRole + 2


# ── 帮助函数 ────────────────────────────────────────────────────────

def format_type(ti) -> str:
    """格式化类型信息为可读字符串。"""
    if ti is None:
        return "?"
    from src.typedefs import (
        BaseType, StructType, ArrayType, PointerType,
        EnumType, TypedefType, FuncType,
    )
    if isinstance(ti, BaseType):
        return ti.name
    if isinstance(ti, StructType):
        prefix = "union " if ti.is_union else "struct "
        name = ti.name if ti.name else "<anonymous>"
        return f"{prefix}{name}"
    if isinstance(ti, ArrayType):
        elem = format_type(ti.element_type)
        return f"{elem}[{ti.count}]"
    if isinstance(ti, PointerType):
        pointed = format_type(ti.pointed_type) if ti.pointed_type else "void"
        return f"{pointed}*"
    if isinstance(ti, EnumType):
        return f"enum {ti.name}"
    if isinstance(ti, TypedefType):
        return ti.name
    if isinstance(ti, FuncType):
        return "func"
    return "?"


def is_base_type(ti) -> bool:
    """判断类型是否为"基础值节点"。

    基础值节点 = 可以被用户勾选、采样、绘制波形、编辑写入。
    非基础值节点（结构体/数组/函数/void*）不可交互。

    规则：
      - BaseType → 是（void 除外）
      - EnumType → 是
      - PointerType → 若指向底层为基础类型 → 是（通过表达式解引用）
      - StructType / ArrayType / FuncType / None → 否
      - TypedefType → 解析 typedef 链递归判断
    """
    from src.typedefs import (
        BaseType, PointerType, TypedefType, EnumType, StructType, ArrayType, FuncType,
    )

    # 解析 typedef 到具体类型
    while isinstance(ti, TypedefType):
        ti = ti.underlying_type

    if isinstance(ti, BaseType):
        return ti.name != "void"  # void 不可写

    if isinstance(ti, EnumType):
        return True

    if isinstance(ti, PointerType):
        pointed = ti.pointed_type
        while isinstance(pointed, TypedefType):
            pointed = pointed.underlying_type
        if pointed is None:
            return False
        # 指向 void 不可写；指向具体基本类型或枚举则可写
        if isinstance(pointed, BaseType):
            return pointed.name != "void"
        return isinstance(pointed, EnumType)

    return False