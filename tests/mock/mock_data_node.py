"""MockDataNode —— 模拟实时采样数据的后端节点。

订阅: StartSamplingRequest, StopSamplingRequest, ChangeSampleRateRequest,
      SelectVariableRequest, VariableWriteRequest
发布: SampleData, SamplingStatus
"""

import logging
import math
import random
import time
from collections import deque

from src.framework import Node
from src.scope import (
    StartSamplingRequest, StopSamplingRequest, ChangeSampleRateRequest,
    SelectVariableRequest, VariableWriteRequest, SampleData, SamplingStatus,
)

logger = logging.getLogger(__name__)


class MockDataNode(Node):
    """模拟数据采集节点。

    收到 SelectVariableRequest 动态维护选中变量列表，
    收到 StartSamplingRequest 后对当前选中变量启动采样循环，
    为每个变量生成带有不同波形特征的模拟数据。
    支持 VariableWriteRequest 修改模拟值。
    """

    def __init__(self, name: str = "MockDataNode"):
        super().__init__(name)
        self._running = False
        self._expressions: list[str] = []
        self._selected: set[str] = set()  # 前端通过勾选发送的选中列表
        self._sample_rate = 100
        self._actual_rate = 0.0
        self._sample_count = 0
        self._t0 = 0.0
        self._phase = 0.0
        # 用户通过 VariableWriteRequest 设置的固定值 { expression: value }
        # 有 override 的变量直接使用此值，不再生成波形
        self._overrides: dict[str, float] = {}
        # 每变量的最新值，用于依赖计算
        self._latest_values: dict[str, float] = {}

        # ── 依赖关系定义：{ 依赖表达式: (计算函数, [输入表达式列表]) } ──
        self._dependencies: dict[str, tuple] = {
            "g_powerStats.power": (
                lambda v, i: v * i,           # power = voltage × current
                ["g_powerStats.voltage", "g_powerStats.current"],
            ),
        }

    def _init(self):
        self.subscribe(StartSamplingRequest, self._on_start)
        self.subscribe(StopSamplingRequest, self._on_stop)
        self.subscribe(ChangeSampleRateRequest, self._on_change_rate)
        self.subscribe(SelectVariableRequest, self._on_select)
        self.subscribe(VariableWriteRequest, self._on_write)

    def _on_select(self, event: SelectVariableRequest):
        """处理前端发来的勾选/取消事件，动态维护选中列表。

        如果采样正在运行，同时动态增减 _expressions，新加入的变量立即开始采样。
        """
        if event.selected:
            self._selected.add(event.expression)
            if self._running and event.expression not in self._expressions:
                self._expressions.append(event.expression)
                self._latest_values[event.expression] = 0.0
        else:
            self._selected.discard(event.expression)
            if self._running and event.expression in self._expressions:
                self._expressions.remove(event.expression)
        logger.info(f"[MockDataNode] 变量 {'勾选' if event.selected else '取消'}: "
                     f"{event.expression}  (共 {len(self._selected)} 个, "
                     f"采样中 {len(self._expressions)} 个)")

    def _on_start(self, event: StartSamplingRequest):
        if self._running:
            return

        self._expressions = sorted(self._selected)
        if not self._expressions:
            logger.warning("[MockDataNode] 没有要采样的变量")
            return

        self._sample_rate = event.sample_rate_hz or 100
        self._sample_count = 0
        self._t0 = 0.0
        self._phase = 0.0
        self._latest_values.clear()
        self._running = True

        self.publish(SamplingStatus(
            is_running=True, sample_count=0, actual_rate=0.0
        ))
        logger.info(f"[MockDataNode] 开始模拟采样: {len(self._expressions)} 变量, "
                     f"{self._sample_rate} Hz")

    def _on_stop(self, event: StopSamplingRequest):
        if not self._running:
            return
        self._running = False
        self.publish(SamplingStatus(
            is_running=False, sample_count=self._sample_count,
            actual_rate=self._actual_rate,
        ))
        logger.info(f"[MockDataNode] 停止采样: {self._sample_count} 样本")

    def _on_change_rate(self, event: ChangeSampleRateRequest):
        """采样中实时调整采样率。"""
        old = self._sample_rate
        self._sample_rate = event.sample_rate_hz
        logger.info(f"[MockDataNode] 采样率: {old} Hz → {self._sample_rate} Hz")

    def _on_write(self, event: VariableWriteRequest):
        """处理前端发来的变量写入请求。

        写入后该变量固定为该值（不再生成波形），
        同时级联更新依赖变量。
        """
        logger.info(f"[MockDataNode] 写入变量: {event.expression} = {event.value}")
        self._overrides[event.expression] = event.value

        # ── 级联更新：如果改的是某依赖的输入，重算该依赖 ──
        self._recompute_dependencies(written_expr=event.expression)

    def _recompute_dependencies(self, written_expr=None):
        """重算所有受 written_expr 影响的依赖变量。
        只有当所有输入都有有效值时才会更新，避免出现 0 值污染。
        """
        for dep_expr, (func, input_exprs) in self._dependencies.items():
            # 只重算与本次写入相关的依赖
            if written_expr is not None and written_expr not in input_exprs and written_expr != dep_expr:
                continue

            args = []
            all_ready = True
            for inp in input_exprs:
                if inp in self._overrides:
                    args.append(self._overrides[inp])
                elif inp in self._latest_values:
                    args.append(self._latest_values[inp])
                else:
                    all_ready = False
                    break

            if not all_ready:
                logger.info(f"  ↳ 跳过 {dep_expr}：输入尚未就绪")
                continue

            computed = func(*args)
            self._overrides[dep_expr] = computed
            logger.info(f"  ↳ 级联更新 {dep_expr} = {computed:.4g}")

    async def _process(self):
        """每帧生成采样数据，每采到一个样本立即发布。"""
        if not self._running:
            return

        now = time.perf_counter()
        if self._t0 == 0.0:
            self._t0 = now

        # 计算本帧应生成的样本数
        elapsed = now - self._t0
        expected = int(elapsed * self._sample_rate)
        to_generate = expected - self._sample_count

        if to_generate <= 0:
            return

        for _ in range(to_generate):
            t = (self._sample_count + 1) / self._sample_rate
            self._phase += 0.05

            # 生成一个独立变量样本
            one_sample: dict[str, float] = {}
            for i, expr in enumerate(self._expressions):
                if expr in self._dependencies:
                    continue
                if expr in self._overrides:
                    val = self._overrides[expr]
                else:
                    val = self._generate_value(expr, i, t)
                one_sample[expr] = val
                self._latest_values[expr] = val

            # 计算依赖变量的这一个样本
            for dep_expr, (func, input_exprs) in self._dependencies.items():
                if dep_expr not in self._expressions:
                    continue
                args = []
                for inp in input_exprs:
                    if inp in self._overrides:
                        args.append(self._overrides[inp])
                    else:
                        args.append(one_sample[inp])
                val = func(*args)
                one_sample[dep_expr] = val
                self._latest_values[dep_expr] = val

            self._sample_count += 1

            # 立即发布这个样本
            self.publish(SampleData(
                buffers=one_sample,
                timestamps=[t],
            ))

        # 更新实际速率
        if elapsed > 0:
            self._actual_rate = self._sample_count / elapsed

    def _generate_value(self, expr: str, index: int, t: float) -> float:
        """为不同变量生成有特征的模拟波形。

        支持 * 前缀表达式（指针解引用），自动剥离后按基名匹配波形。
        """
        # 剥离 * 前缀（指针解引用），按实际变量名匹配波形
        display_name = expr.lstrip('*')
        name = display_name.split(".")[-1] if "." in display_name else display_name
        noise = (hash(expr) % 100) / 100 * 0.05

        if "speed" in name.lower():
            return 800 + 200 * math.sin(self._phase + index * 0.5) + noise

        if "temperature" in name.lower() or "temp" in name.lower():
            base = 25.0 + 5 * math.sin(t * 0.02 + index)
            return base + noise * 0.5

        if "voltage" in name.lower():
            return 3.3 + 0.05 * math.sin(self._phase * 2) + noise * 0.2

        if "current" in name.lower():
            return 0.5

        if "pulse" in name.lower() or "pwm" in name.lower():
            duty = 0.5 + 0.3 * math.sin(t * 0.5)
            return duty + noise * 0.1

        if "pid" in name.lower() or "kp" in name.lower() or "ki" in name.lower():
            base = {"kp": 2.0, "ki": 0.5, "kd": 0.1}.get(name.lower(), 1.0)
            return base + noise * 0.02

        if "error" in name.lower():
            return 0.0 if int(t) % 30 < 28 else float(random.randint(1, 5))

        if "flag" in name.lower() or "status" in name.lower():
            return float(random.randint(0, 5))

        if "gimbal" in name.lower() or "yaw" in name.lower():
            return 90 + 45 * math.sin(self._phase * 0.3 + index * 0.7) + noise

        if "pitch" in name.lower():
            return 45 + 30 * math.sin(self._phase * 0.4 + index * 0.3) + noise

        if "encoder" in name.lower() or "raw" in name.lower():
            return 2048 + 1500 * math.sin(self._phase * 0.5) + noise * 100

        if "angle" in name.lower():
            return 180 * math.sin(self._phase * 0.3) + noise * 5

        if "adc" in name.lower() or "buffer" in name.lower():
            return 2048 + 1024 * math.sin(self._phase * 2) + noise * 200

        if "tick" in name.lower():
            return t * 1000

        return 50 + 30 * math.sin(self._phase * 0.5 + index) + noise * 5