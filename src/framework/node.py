import asyncio
import logging
from typing import Any, Callable, Type, Union

from .event import Event

logger = logging.getLogger(__name__)

class Node:
    def __init__(self, name: str):
        self.name = name
        self._bus = None

    def _set_bus(self, bus):
        self._bus = bus

    # 钩子（可选）
    def _init(self):
        pass

    async def _ready(self):
        pass

    async def _process(self):
        pass

    def publish(self, event: Event) -> Event:
        if self._bus is None:
            raise RuntimeError(f"Node '{self.name}' not attached to a Container.")
        object.__setattr__(event, '_source_node', self.name)
        event_json = event.model_dump_json()
        logger.info(f"[{self.name}] | {event_json}")
        try:
            asyncio.get_running_loop()
            self._bus.dispatch(event)
        except RuntimeError:
            asyncio.run(self._bus.dispatch_async(event))
        return event

    def subscribe(self, event_type: Union[Type[Event], str], handler: Callable[[Event], Any]):
        if self._bus is None:
            raise RuntimeError(f"Node '{self.name}' not attached to a Container.")

        async def async_wrapper(event: Event):
            event_json = event.model_dump_json()
            src = getattr(event, '_source_node', '?')
            logger.info(f"[{src}] -> [{self.name}] | {event_json}")
            try:
                return await handler(event)
            except Exception as e:
                logger.error(f"[ERROR] [{self.name}] | {event_json} | {e}")
                raise

        def sync_wrapper(event: Event):
            event_json = event.model_dump_json()
            src = getattr(event, '_source_node', '?')
            logger.info(f"[{src}] -> [{self.name}] | {event_json}")
            try:
                return handler(event)
            except Exception as e:
                logger.error(f"[ERROR] [{self.name}] | {event_json} | {e}")
                raise

        wrapped = async_wrapper if asyncio.iscoroutinefunction(handler) else sync_wrapper
        # 避免 bubus 警告，给包装函数一个唯一名称
        if isinstance(event_type, type) and issubclass(event_type, Event):
            type_name = event_type.__name__
        else:
            type_name = str(event_type).replace('.', '_')
        wrapped.__name__ = f"{self.name}_handler_{type_name}"
        self._bus.on(event_type, wrapped)