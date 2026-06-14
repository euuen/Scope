"""Scope 主入口 —— 组装所有 src/ 节点启动容器。"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

from src.framework import Container
from src.scope import ScopeNode
from src.probe import ProbeNode
from src.elf import ElfNode


def main():
    container = Container(name="Scope")

    container.add_node(ScopeNode())
    container.add_node(ElfNode())
    container.add_node(ProbeNode())

    logger = logging.getLogger(__name__)
    logger.info("Scope 启动:")
    logger.info("  ScopeNode  前端 UI")
    logger.info("  ElfNode    ELF 解析")
    logger.info("  ProbeNode  探针 + 采样")

    container.run()


if __name__ == "__main__":
    main()