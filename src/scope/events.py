"""Frontend 事件定义 —— 前端 UI 与后端节点之间的通信协议。"""

from src.framework import Event


# ================================================================
#  前端 → 后端（用户请求类）
# ================================================================

class ImportElfRequest(Event):
    """用户请求导入 ELF/AXF 文件。

    Fields:
        path: ELF/AXF 文件的绝对路径
    """
    path: str


class ScanProbesRequest(Event):
    """用户请求扫描调试探针。"""
    pass


class ConnectProbeRequest(Event):
    """用户请求连接调试探针。

    Fields:
        probe_index: 探针在列表中的索引
        mode: 连接模式 ("attach" / "reset")
        swd_freq_hz: SWD 时钟频率 (Hz)
        target_override: 强制指定目标芯片型号，如 "stm32g0b1re"。留空则由 pyOCD 自动识别。
    """
    probe_index: int = 0
    mode: str = "attach"
    swd_freq_hz: int = 4000000
    target_override: str = ""


class DisconnectProbeRequest(Event):
    """用户请求断开探针连接。"""
    pass


class SelectVariableRequest(Event):
    """用户勾选/取消勾选变量。

    每次勾选或取消都立即发送，后端自己维护已选列表。

    Fields:
        expression: 变量表达式，如 "g_powerStats.current"
        selected: True=勾选, False=取消
    """
    expression: str = ""
    selected: bool = False


class StartSamplingRequest(Event):
    """用户请求开始采样。

    Fields:
        sample_rate_hz: 目标采样率 (Hz)
        buffer_seconds: 缓冲区时长 (秒)
    """
    sample_rate_hz: int = 100
    buffer_seconds: int = 300


class ChangeSampleRateRequest(Event):
    """用户调整采样率（下拉选择），后端即时响应。

    Fields:
        sample_rate_hz: 新的采样率 (Hz)
    """
    sample_rate_hz: int = 100


class StopSamplingRequest(Event):
    """用户请求停止采样。"""
    pass


class VariableWriteRequest(Event):
    """用户请求写入变量值。

    Fields:
        expression: 变量表达式，如 "g_powerStats.current"
        value: 要写入的值
    """
    expression: str = ""
    value: float = 0.0


class PauseMcuRequest(Event):
    """暂停 MCU 运行。"""
    pass


class ResumeMcuRequest(Event):
    """恢复 MCU 运行。"""
    pass


class ResetSamplingRequest(Event):
    """全量复位：清空 MockDataNode 内部所有计数器和相位。"""
    pass


# ================================================================
#  后端 → 前端（状态响应类）
# ================================================================

class ElfLoaded(Event):
    """ELF 文件加载完成。

    Fields:
        path: 文件路径
        variables: Variable 对象列表
        symbol_count: 符号总数
        file_count: 源文件数量
        values: { "变量路径": 初始值, ... }  解析时从 ELF 数据段读取的初始值
    """
    path: str
    variables: list = None
    symbol_count: int = 0
    file_count: int = 0
    values: dict = None


class ElfLoadFailed(Event):
    """ELF 文件加载失败。

    Fields:
        path: 文件路径
        reason: 失败原因
    """
    path: str
    reason: str


class ProbeScanResult(Event):
    """探针扫描结果。

    Fields:
        probes: 探针信息字典列表
            [{ "name": str, "vendor": str, "uid": str, ... }, ...]
    """
    probes: list = None


class ProbeConnected(Event):
    """探针已成功连接。

    Fields:
        target_name: 目标芯片名称
        swd_freq_khz: 实际 SWD 频率 (kHz)
        probe_name: 探针名称
    """
    target_name: str = "Unknown"
    swd_freq_khz: int = 4000
    probe_name: str = ""


class ProbeDisconnected(Event):
    """探针已断开连接。"""
    pass


class ProbeConnectionFailed(Event):
    """探针连接失败。

    Fields:
        reason: 失败原因描述
    """
    reason: str


class SampleData(Event):
    """实时采样数据更新（每样本一条）。

    Fields:
        buffers: { path: value, ... }  每个变量的单个采样值
        timestamps: [float]  单个时间戳
    """
    buffers: dict = None
    timestamps: list = None


class SamplingStatus(Event):
    """采样状态更新。

    Fields:
        is_running: 是否正在采样
        sample_count: 已采样本数
        actual_rate: 实际采样率 (Hz)
        paused: MCU 是否暂停
    """
    is_running: bool = False
    sample_count: int = 0
    actual_rate: float = 0.0
    paused: bool = False