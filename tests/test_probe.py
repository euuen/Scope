"""ProbeNode 测试 —— 仅组装探针节点，供硬件测试使用。

组装方式:
    Container
    ├── ScopeNode   (前端 UI)
    └── ProbeNode   (pyOCD 硬件探针)

启动后连接实际调试器即可操作。无需任何 mock 代码。
"""

import sys
import logging
from pathlib import Path

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
from src.probe import ProbeNode


def main():
    container = Container(name="ProbeTestContainer")

    scope = ScopeNode("ScopeNode")
    probe = ProbeNode("ProbeNode")

    container.add_node(scope)
    container.add_node(probe)

    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("ProbeNode 硬件测试容器")
    logger.info("  - ScopeNode: 前端 UI")
    logger.info("  - ProbeNode: pyOCD 硬件探针")
    logger.info("=" * 50)
    logger.info("")
    logger.info("操作指引:")
    logger.info("  1. 连接调试器到目标板")
    logger.info("  2. 点击「🔄 扫描」→ 发现硬件探针")
    logger.info("  3. 选择探针，点击「连接」")
    logger.info("  4. 导入 ELF，勾选变量，开始采样")
    logger.info("")

    container.run()


if __name__ == "__main__":
    main()