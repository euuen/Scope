"""ELF 解析模块 —— 纯 Python ELF 二进制解析 + DWARF 类型解析 + 变量归并。"""

from .node import ElfNode
from .parser import ELFParser, parse_elf, ElfParseResult, ElfHeader, ElfSection, ElfSymbol
from .readelf import DwarfDB, parse_debug_info, parse_line_table, parse_symbol_table
from .inventory import VariableInventory
from .initial_values import read_initial_values

__all__ = [
    "ElfNode",
    "ELFParser",
    "parse_elf",
    "ElfParseResult",
    "ElfHeader",
    "ElfSection",
    "ElfSymbol",
    "DwarfDB",
    "parse_debug_info",
    "parse_line_table",
    "parse_symbol_table",
    "VariableInventory",
    "read_initial_values",
]