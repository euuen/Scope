"""VariableInventory —— 合并 ELF 符号表 + DWARF 类型信息 + 源文件映射。

完全移植自 old/src/parser/variable_inventory.py。

流程:
  1. 从二进制 ELF 解析获取符号表（ElfSymbol）
  2. 从 DWARF 解析获取带类型的变量（Variable，含 type_info、file_name）
  3. 按地址 + 名称合并：符号表提供地址/大小，DWARF 提供类型/源文件
  4. 输出完整的 Variable 对象列表
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from src.typedefs import (
    Variable, Symbol, BaseType, StructType, ArrayType,
    PointerType, EnumType, TypedefType, FuncType, TypeInfo,
)
from src.elf.parser import ElfSymbol
from src.elf.readelf import DwarfDB


def _match_lto_suffix(suffixed: str, base: str) -> bool:
    """LTO 后缀匹配：如 'foo.4' 匹配 'foo'"""
    m = _LTO_SUFFIX_RE.match(suffixed)
    return m is not None and m.group(1) == base


def _clean_dwarf_path(path: str) -> str:
    """清理 DWARF 源文件路径用于显示。"""
    if not path:
        return ""
    p = Path(path.replace('\\', '/'))
    if p.suffix == ".h" or "/include/" in path or "/Include/" in path:
        return p.name
    for marker in ["/Core/", "/Drivers/", "/lib/", "/app/", "/bsp/"]:
        idx = path.find(marker)
        if idx >= 0:
            return path[idx + 1:]
    return p.name


# LTO 会生成 "var.3", "var.4" 等重复符号
_LTO_SUFFIX_RE = re.compile(r"^(.*)(?:\.\d+)$")


class VariableInventory:
    """合并 ELF 符号和 DWARF 类型信息，生成最终的 Variable 列表。"""

    def __init__(
        self,
        elf_symbols: list[ElfSymbol],
        dwarf_db: Optional[DwarfDB] = None,
        symbol_to_file: Optional[dict[str, str]] = None,
    ):
        self.elf_symbols = elf_symbols
        self.dwarf_db = dwarf_db
        self.symbol_to_file = symbol_to_file or {}

    def generate(self) -> list[Variable]:
        """生成最终的 Variable 列表。"""
        # ── DWARF 索引 ──
        dwarf_by_addr: dict[int, Variable] = {}
        dwarf_by_name: dict[str, Variable] = {}
        if self.dwarf_db and self.dwarf_db.has_debug_info():
            for v in self.dwarf_db.variables:
                if v.address != 0:
                    dwarf_by_addr[v.address] = v
                if v.name not in dwarf_by_name or v.address != 0:
                    dwarf_by_name[v.name] = v

        variables: list[Variable] = []
        seen_names: set[str] = set()
        seen_addrs: dict[int, int] = {}  # addr → index in variables

        # ── 遍历 ELF 符号，与 DWARF 合并 ──
        for sym in self.elf_symbols:
            if sym.sym_type != "OBJECT":
                continue
            if sym.address == 0:
                continue
            if not sym.name or sym.name.startswith(".L") or sym.name.startswith("$"):
                continue

            type_info = None
            file_name = self.symbol_to_file.get(sym.name, "")
            dv = dwarf_by_addr.get(sym.address) or dwarf_by_name.get(sym.name)
            if dv and dv.type_info:
                type_info = dv.type_info
                if not file_name:
                    file_name = _clean_dwarf_path(dv.file_name)

            # LTO: 剥离 .N 后缀
            display_name = sym.name
            if dv and dv.name != sym.name and _match_lto_suffix(sym.name, dv.name):
                display_name = dv.name

            # 同一地址去重：优先保留带类型或非 LTO 名
            prev_idx = seen_addrs.get(sym.address)
            if prev_idx is not None:
                prev_var = variables[prev_idx]
                prev_has_type = prev_var.type_info is not None
                cur_has_type = type_info is not None
                prev_is_lto = bool(_LTO_SUFFIX_RE.match(prev_var.name))
                cur_is_lto = bool(_LTO_SUFFIX_RE.match(display_name))
                if (not prev_has_type and cur_has_type) or (prev_is_lto and not cur_is_lto):
                    variables[prev_idx] = Variable(
                        name=display_name, address=sym.address, size=sym.size,
                        type_info=type_info, symbol=Symbol(
                            name=sym.name, address=sym.address, size=sym.size,
                            binding=sym.binding, sym_type=sym.sym_type, section=sym.section,
                        ),
                        file_name=file_name,
                    )
                    if prev_var.name in seen_names:
                        seen_names.discard(prev_var.name)
                    seen_names.add(display_name)
                    seen_addrs[sym.address] = prev_idx
                continue

            variables.append(Variable(
                name=display_name,
                address=sym.address,
                size=sym.size,
                type_info=type_info,
                symbol=Symbol(
                    name=sym.name, address=sym.address, size=sym.size,
                    binding=sym.binding, sym_type=sym.sym_type, section=sym.section,
                ),
                file_name=file_name,
            ))
            seen_names.add(display_name)
            seen_addrs[sym.address] = len(variables) - 1

        # ── DWARF-only 变量（ELF 符号表未覆盖的）──
        if self.dwarf_db and self.dwarf_db.has_debug_info():
            for dv in self.dwarf_db.variables:
                if dv.name in seen_names:
                    continue
                if dv.address == 0:
                    continue

                prev_idx = seen_addrs.get(dv.address)
                if prev_idx is not None:
                    prev_var = variables[prev_idx]
                    prev_has_type = prev_var.type_info is not None
                    prev_is_lto = bool(_LTO_SUFFIX_RE.match(prev_var.name))
                    if not prev_has_type or prev_is_lto:
                        if prev_var.name in seen_names:
                            seen_names.discard(prev_var.name)
                        seen_names.add(dv.name)
                        variables[prev_idx] = Variable(
                            name=dv.name, address=dv.address, size=dv.size,
                            type_info=dv.type_info, symbol=prev_var.symbol,
                            file_name=_clean_dwarf_path(dv.file_name) or prev_var.file_name,
                        )
                        seen_addrs[dv.address] = prev_idx
                    continue

                map_file = self.symbol_to_file.get(dv.name, "")
                if map_file:
                    dv.file_name = map_file
                elif dv.file_name:
                    dv.file_name = _clean_dwarf_path(dv.file_name)
                variables.append(dv)
                seen_names.add(dv.name)
                seen_addrs[dv.address] = len(variables) - 1

        variables.sort(key=lambda v: v.address)
        return variables