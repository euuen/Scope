"""ElfNode —— 真正的 ELF 文件解析后端节点。

完整流程（参考 old/src/parser/readelf.py + variable_inventory.py）:
  1. 二进制 ELF 解析器 → 符号表、节区、.data 初始值
  2. arm-none-eabi-readelf -wi → DWARF DIE 树 → 类型信息、结构体、枚举、数组
  3. VariableInventory → 合并符号表 + DWARF + 源文件
  4. 发布 ElfLoaded（含完整类型信息）

订阅: ImportElfRequest
发布: ElfLoaded / ElfLoadFailed
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.framework import Node
from src.scope import ImportElfRequest, ElfLoaded, ElfLoadFailed
from src.elf.parser import ELFParser
from src.elf.readelf import parse_debug_info, DwarfDB
from src.elf.inventory import VariableInventory
from src.elf.initial_values import read_initial_values

logger = logging.getLogger(__name__)


class ElfNode(Node):
    """ELF 解析节点。

    完整流程:
      ImportElfRequest
        → 二进制解析 ELF header/sections/symbols/.data
        → DWARF 解析类型信息（通过 arm-none-eabi-readelf -wi）
        → VariableInventory 合并符号 + 类型 + 源文件
        → read_initial_values() 读取 .data 初始值（全表达式支持）
        → 发布 ElfLoaded
    """

    def __init__(self, name: str = "ElfNode"):
        super().__init__(name)

    def _init(self):
        self.subscribe(ImportElfRequest, self._on_import)

    # ── 事件入口 ────────────────────────────────────────────

    def _on_import(self, event: ImportElfRequest):
        path = event.path
        logger.info(f"[ElfNode] 开始解析 ELF: {path}")

        if not Path(path).exists():
            self.publish(ElfLoadFailed(path=path, reason=f"文件不存在: {path}"))
            return

        # ── 1. 二进制解析 ELF（符号表、节区） ──
        try:
            elf_result = ELFParser.from_file(path).parse()
        except Exception as e:
            logger.error(f"[ElfNode] 二进制解析失败: {e}")
            self.publish(ElfLoadFailed(path=path, reason=f"二进制解析失败: {e}"))
            return

        # ── 2. DWARF 解析（类型信息、源文件） ──
        dwarf_db = DwarfDB()
        try:
            dwarf_db = parse_debug_info(path)
            if dwarf_db.has_debug_info():
                logger.info(f"[ElfNode] DWARF 解析成功: {len(dwarf_db.types)} 个类型, "
                            f"{len(dwarf_db.variables)} 个变量")
            else:
                logger.info("[ElfNode] 无 DWARF 调试信息，仅使用符号表")
        except Exception as e:
            logger.warning(f"[ElfNode] DWARF 解析失败（将降级为仅符号表）: {e}")

        # ── 3. VariableInventory 合并 ──
        inventory = VariableInventory(
            elf_symbols=elf_result.symbols,
            dwarf_db=dwarf_db if dwarf_db.has_debug_info() else None,
        )
        variables = inventory.generate()

        # ── 4. 读取 .data 节区初始值（委托给 read_initial_values） ──
        values = read_initial_values(elf_result, variables)

        # ── 5. 统计源文件数 ──
        file_set: set[str] = set()
        for v in variables:
            if v.file_name:
                file_set.add(v.file_name)
        for f in elf_result.files:
            file_set.add(f)

        # ── 发布结果 ──
        self.publish(ElfLoaded(
            path=path,
            variables=variables,
            symbol_count=len(elf_result.symbols),
            file_count=len(file_set),
            values=values,
        ))

        logger.info(
            f"[ElfNode] 解析完成: {len(variables)} 个变量, "
            f"{len(values)} 个初始值, {len(file_set)} 个源文件, "
            f"{len(elf_result.symbols)} 个符号"
        )