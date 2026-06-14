"""Scope 前端测试 —— 完整的事件总线集成测试。

组装所有 mock 节点和前端 ScopeNode，启动容器进行交互验证。
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
from tests.mock.mock_elf_node import MockElfNode
from tests.mock.mock_probe_node import MockProbeNode
from tests.mock.mock_data_node import MockDataNode


def main():
    """构建测试容器并运行。

    组装方式:
        Container
        ├── ScopeNode      (前端 UI)
        ├── MockElfNode    (模拟 ELF 加载)
        ├── MockProbeNode  (模拟探针管理)
        └── MockDataNode   (模拟数据采集)
    """
    container = Container(name="ScopeTestContainer")

    scope = ScopeNode("ScopeNode")
    elf = MockElfNode("MockElfNode")
    probe = MockProbeNode("MockProbeNode")
    data = MockDataNode("MockDataNode")

    container.add_node(scope)
    container.add_node(elf)
    container.add_node(probe)
    container.add_node(data)

    # 自动导入一个模拟 ELF（用当前脚本自身作为占位文件）
    # 在 UI 中也可以手动点击「导入 ELF」按钮
    self_path = str(Path(__file__).resolve())
    from src.scope import ImportElfRequest

    # 延迟一点触发自动导入，确保 UI 已显示
    import asyncio

    async def auto_import():
        await asyncio.sleep(1.0)
        scope.publish(ImportElfRequest(path=self_path))
        logger = logging.getLogger(__name__)
        logger.info("已自动触发模拟 ELF 导入")

    # 注入到容器的 _run_async 中
    original_run = container._run_async

    async def patched_run():
        await auto_import()
        await original_run()

    container._run_async = patched_run

    logger = logging.getLogger(__name__)
    logger.info("=" * 50)
    logger.info("Scope 测试容器启动")
    logger.info("  - ScopeNode: 前端 UI (PySide6 + pyqtgraph)")
    logger.info("  - MockElfNode: 模拟 ELF 加载 → {变量树}")
    logger.info("  - MockProbeNode: 模拟探针扫描/连接")
    logger.info("  - MockDataNode: 模拟实时数据采样 → {波形}")
    logger.info("=" * 50)
    logger.info("")
    logger.info("操作指引:")
    logger.info("  1. 等待 1s → 自动加载模拟 ELF（变量树填充）")
    logger.info("  2. 点击「🔄 扫描」→ 发现 3 个模拟探针")
    logger.info("  3. 点击「连接」→ 模拟连接成功")
    logger.info("  4. 在左侧勾选变量，点击「开始」→ 波形开始绘制")
    logger.info("  5. 点击「停止」→ 停止采样")
    logger.info("  6. 点击「导出 CSV」→ 导出数据")
    logger.info("")

    container.run()


if __name__ == "__main__":
    main()