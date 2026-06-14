"""ElfNode 前端测试 —— 真实的 ELF 解析 + 事件总线集成测试。

组装 ScopeNode（前端 UI）+ ElfNode（真正解析 ELF），启动容器。
用户点击「导入 ELF」按钮后，ElfNode 会实际解析二进制 ELF 文件。
"""

import sys
import logging
from pathlib import Path

# 确保 Scope 目录在 sys.path 中
scope_dir = Path(__file__).resolve().parent.parent
if str(scope_dir) not in sys.path:
    sys.path.insert(0, str(scope_dir))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

from src.framework import Container
from src.scope import ScopeNode
from src.elf import ElfNode


def main():
    """构建测试容器并运行。

    组装方式:
        Container
        ├── ScopeNode      (前端 UI)
        └── ElfNode        (真实 ELF 解析)
    """
    container = Container(name="ElfTestContainer")

    scope = ScopeNode("ScopeNode")
    elf = ElfNode("ElfNode")

    container.add_node(scope)
    container.add_node(elf)

    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("ElfNode 测试容器启动")
    logger.info("  - ScopeNode: 前端 UI (PySide6 + pyqtgraph)")
    logger.info("  - ElfNode:   实 ELF 解析 → {变量树}")
    logger.info("=" * 50)
    logger.info("")
    logger.info("操作指引:")
    logger.info("  1. 点击「导入 ELF」按钮 (Ctrl+E)")
    logger.info("  2. 选择一个真实的 .elf / .axf 文件")
    logger.info("  3. 观察左侧变量树中的符号列表")
    logger.info("     - 名称、地址、大小均来自 ELF 符号表")
    logger.info("     - 初始值来自 .data 节区")
    logger.info("  4. 可勾选变量后配合 MockProbeNode + MockDataNode 做采样测试")
    logger.info("")

    container.run()


if __name__ == "__main__":
    main()