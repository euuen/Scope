import asyncio
import logging
import re
from typing import List

from bubus import EventBus

from .event import Event, CloseApplication
from .node import Node

logger = logging.getLogger(__name__)

class _TraceEventBus(EventBus):
    def dispatch(self, event: Event) -> Event:
        logger.debug(f"[TRACE] Publishing event: {event}")
        return super().dispatch(event)

class _SystemNode(Node):
    """Container 内部系统节点，用于处理内置事件（如 CloseApplication）"""
    def __init__(self, stop_event: asyncio.Event):
        super().__init__("__system__")
        self._stop_event = stop_event

    def _init(self):
        self.subscribe(CloseApplication, self._on_close)

    async def _on_close(self, event: CloseApplication):
        self._stop_event.set()


class Container:
    def __init__(self, name: str = "DefaultContainer"):
        self.name = name
        safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        self._bus = _TraceEventBus(name=f"{safe_name}_bus")
        self._nodes: List[Node] = []
        self._stop_event = asyncio.Event()
        self._add_system_nodes()

    def _add_system_nodes(self):
        system = _SystemNode(self._stop_event)
        self.add_node(system)

    def add_node(self, node: Node):
        if node in self._nodes:
            raise ValueError(f"Node '{node.name}' already added.")
        node._set_bus(self._bus)
        self._nodes.append(node)
        if hasattr(node, '_init') and callable(node._init):
            node._init()
        # 没有任何 logger.info

    def get_nodes(self) -> List[Node]:
        return self._nodes.copy()

    def run(self):
        try:
            asyncio.run(self._run_async())
        except KeyboardInterrupt:
            pass  # 静默退出

    async def _run_async(self):
        # 不输出任何启动日志
        for node in self._nodes:
            if hasattr(node, '_ready') and callable(node._ready):
                if asyncio.iscoroutinefunction(node._ready):
                    await node._ready()
                else:
                    await asyncio.to_thread(node._ready)

        while not self._stop_event.is_set():
            for node in self._nodes:
                if hasattr(node, '_process') and callable(node._process):
                    if asyncio.iscoroutinefunction(node._process):
                        await node._process()
                    else:
                        await asyncio.to_thread(node._process)
            await asyncio.sleep(0.001)  # 1ms 让步，避免忙循环占用 CPU

        await self._bus.stop()
        # 不输出停止日志