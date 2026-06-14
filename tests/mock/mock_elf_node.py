"""MockElfNode —— 模拟 ELF 文件加载的后端节点。

订阅: ImportElfRequest
发布: ElfLoaded / ElfLoadFailed
"""

import logging
from pathlib import Path

from src.framework import Node
from src.scope import ImportElfRequest, ElfLoaded, ElfLoadFailed
from src.typedefs import (
    Variable, BaseType, StructType, MemberInfo, PointerType, ArrayType, EnumType,
)

logger = logging.getLogger(__name__)


class MockElfNode(Node):
    """模拟 ELF 解析器节点。

    收到 ImportElfRequest 后，生成一组模拟变量并发布 ElfLoaded 事件。
    """

    def __init__(self, name: str = "MockElfNode"):
        super().__init__(name)

    def _init(self):
        self.subscribe(ImportElfRequest, self._on_import)

    def _on_import(self, event: ImportElfRequest):
        path = event.path
        logger.info(f"[MockElfNode] 模拟加载: {path}")

        if not Path(path).exists():
            self.publish(ElfLoadFailed(path=path, reason=f"文件不存在: {path}"))
            return

        # ── 生成模拟变量 ──
        vars_list = [
            Variable("g_systemTick", 0x20000000, 4, BaseType("uint32_t", 4), file_name="main.c"),
            Variable("g_temperature", 0x20000004, 4, BaseType("float", 4), file_name="main.c"),
            Variable("g_voltage", 0x20000008, 2, BaseType("uint16_t", 2), file_name="adc.c"),
            Variable("g_current", 0x2000000A, 2, BaseType("int16_t", 2), file_name="adc.c"),
            Variable("g_pulseWidth", 0x2000000C, 4, BaseType("uint32_t", 4), file_name="pwm.c"),
            Variable("g_targetSpeed", 0x20000010, 4, BaseType("float", 4), file_name="motor.c"),
            Variable("g_actualSpeed", 0x20000014, 4, BaseType("float", 4), file_name="motor.c"),
            Variable("g_errorCode", 0x20000018, 1, BaseType("uint8_t", 1), file_name="system.c"),
            Variable("g_statusFlags", 0x2000001C, 4, BaseType("uint32_t", 4), file_name="system.c"),
            Variable("g_pid_Kp", 0x20000020, 4, BaseType("float", 4), file_name="pid.c"),
            Variable("g_pid_Ki", 0x20000024, 4, BaseType("float", 4), file_name="pid.c"),
            Variable("g_pid_Kd", 0x20000028, 4, BaseType("float", 4), file_name="pid.c"),
        ]

        # ── 结构体变量 ──
        gimbal_type = StructType("Gimbal_t", 24, [
            MemberInfo("yaw", 0, BaseType("float", 4)),
            MemberInfo("pitch", 4, BaseType("float", 4)),
            MemberInfo("yawTarget", 8, BaseType("float", 4)),
            MemberInfo("pitchTarget", 12, BaseType("float", 4)),
            MemberInfo("enabled", 16, BaseType("uint8_t", 1)),
            MemberInfo("mode", 20, BaseType("uint8_t", 1)),
        ])
        vars_list.append(Variable("g_gimbal", 0x20000100, 24, gimbal_type, file_name="gimbal.c"))

        # ── 带依赖关系的结构体：power = voltage × current ──
        power_type = StructType("PowerStats_t", 12, [
            MemberInfo("voltage", 0, BaseType("float", 4)),
            MemberInfo("current", 4, BaseType("float", 4)),
            MemberInfo("power", 8, BaseType("float", 4)),
        ])
        vars_list.append(Variable("g_powerStats", 0x20000500, 12, power_type, file_name="power.c"))

        encoder_type = StructType("Encoder_t", 8, [
            MemberInfo("raw", 0, BaseType("uint16_t", 2)),
            MemberInfo("angle", 2, BaseType("int16_t", 2)),
            MemberInfo("offset", 4, BaseType("uint16_t", 2)),
            MemberInfo("error", 6, BaseType("uint8_t", 1)),
        ])
        vars_list.append(Variable("g_chassisEncoder", 0x20000200, 8, encoder_type, file_name="encoder.c"))

        # ── 数组变量 ──
        vars_list.append(Variable("g_adcBuffer", 0x20000300, 64,
            ArrayType(BaseType("uint16_t", 2), 32, 64), file_name="adc.c"))

        # ── 指针变量 ──
        vars_list.append(Variable("g_pConfig", 0x20000400, 4,
            PointerType(BaseType("void", 0), 4), file_name="config.c"))

        # ── 枚举变量 ──
        vars_list.append(Variable("g_motorState", 0x20000404, 4,
            EnumType("MotorState", 4, [
                ("MOTOR_IDLE", 0), ("MOTOR_RUNNING", 1),
                ("MOTOR_STOP", 2), ("MOTOR_ERROR", 3),
            ]), file_name="motor.c"))

        # ── 数组内指向结构体的指针（测试展开）──
        uart_config_type = StructType("UartConfig_t", 12, [
            MemberInfo("baud_rate", 0, BaseType("uint32_t", 4)),
            MemberInfo("data_bits", 4, BaseType("uint8_t", 1)),
            MemberInfo("stop_bits", 5, BaseType("uint8_t", 1)),
            MemberInfo("parity", 8, BaseType("uint8_t", 1)),
        ])
        vars_list.append(Variable("g_uartConfigs", 0x20000600, 12,
            ArrayType(PointerType(uart_config_type, 4), 3, 12),
            file_name="uart.c"))

        # 收集源文件数量
        files = set(v.file_name for v in vars_list if v.file_name)

        # ── 生成初始值 ──
        # 模拟从 ELF 数据段读取的运行时初始值
        values: dict[str, float] = {
            "g_systemTick": 0.0,
            "g_temperature": 25.5,
            "g_voltage": 3.3,
            "g_current": 0.5,
            "g_pulseWidth": 1500.0,
            "g_targetSpeed": 800.0,
            "g_actualSpeed": 795.3,
            "g_errorCode": 0.0,
            "g_statusFlags": 5.0,
            "g_pid_Kp": 2.0,
            "g_pid_Ki": 0.5,
            "g_pid_Kd": 0.1,
            "g_gimbal.yaw": 90.0,
            "g_gimbal.pitch": 45.0,
            "g_gimbal.yawTarget": 90.0,
            "g_gimbal.pitchTarget": 45.0,
            "g_gimbal.enabled": 1.0,
            "g_gimbal.mode": 2.0,
            "g_chassisEncoder.raw": 2048.0,
            "g_chassisEncoder.angle": 180.0,
            "g_chassisEncoder.offset": 512.0,
            "g_chassisEncoder.error": 0.0,
            "g_motorState": 1.0,
            "g_pConfig": 0x20001000,
            # PowerStats —— power = voltage × current
            "g_powerStats.voltage": 3.3,
            "g_powerStats.current": 0.5,
            "g_powerStats.power": 3.3 * 0.5,  # = 1.65
            # Device — 指针指向结构体（测试指针展开）
            "g_device.id": 1.0,
            "g_device.*config.baud_rate": 115200.0,
            "g_device.*config.data_bits": 8.0,
            "g_device.*config.stop_bits": 1.0,
            # 数组内指针→结构体（测试展开）
            "*g_uartConfigs[0].baud_rate": 115200.0,
            "*g_uartConfigs[0].data_bits": 8.0,
            "*g_uartConfigs[0].stop_bits": 1.0,
            "*g_uartConfigs[0].parity": 0.0,
            "*g_uartConfigs[1].baud_rate": 9600.0,
            "*g_uartConfigs[1].data_bits": 7.0,
            "*g_uartConfigs[1].stop_bits": 2.0,
            "*g_uartConfigs[1].parity": 1.0,
            "*g_uartConfigs[2].baud_rate": 57600.0,
            "*g_uartConfigs[2].data_bits": 8.0,
            "*g_uartConfigs[2].stop_bits": 1.0,
            "*g_uartConfigs[2].parity": 0.0,
        }
        # 数组元素初始值
        for i in range(32):
            values[f"g_adcBuffer[{i}]"] = 2048.0 + 1024.0 * (i % 4 - 1.5)

        self.publish(ElfLoaded(
            path=path,
            variables=vars_list,
            symbol_count=len(vars_list),
            file_count=len(files),
            values=values,
        ))

        logger.info(f"[MockElfNode] 发布了 {len(vars_list)} 个模拟变量 "
                     f"(来自 {len(files)} 个源文件, {len(values)} 个初始值)")