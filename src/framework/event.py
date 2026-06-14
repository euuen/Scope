from bubus import BaseEvent

class Event(BaseEvent):
    """所有自定义事件的基类"""
    pass

class CloseApplication(Event):
    """内置关闭事件，发布此事件会优雅停止 Container"""
    pass