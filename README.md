# LoopMaster Scope — 事件总线协议文档

## 概述

Scope 采用 **事件驱动架构**，通过 `bubus` 事件总线和 `Container/Node` 框架实现前后端松耦合通信。

所有节点之间不直接调用，而是通过**发布/订阅**特定事件类型进行交互。

> 📁 事件全部定义在 `src/scope/events.py`

---

## 事件分类

### 🔴 前端 → 后端（用户操作请求）

这些事件由 `ScopeNode`（前端 UI）在用户操作时发布，后端节点负责响应。

| 事件 | 触发时机 | 载荷字段 | 预期接收者 |
|------|----------|----------|------------|
| `ImportElfRequest` | 用户点击「导入 ELF」或 Ctrl+E | `path: str` | `MockElfNode` |
| `ScanProbesRequest` | 用户点击「扫描」 | _(无)_ | `MockProbeNode` |
| `ConnectProbeRequest` | 用户点击「连接」 | `probe_index, mode, swd_freq_hz` | `MockProbeNode` |
| `DisconnectProbeRequest` | 用户点击「断开」 | _(无)_ | `MockProbeNode` |
| `SelectVariableRequest` | 用户勾选/取消变量 | `expression, selected` | `MockDataNode` |
| `StartSamplingRequest` | 用户点击「开始」 | `sample_rate_hz, buffer_seconds` | `MockDataNode` |
| `StopSamplingRequest` | 用户点击「停止」 | _(无)_ | `MockDataNode` |
| `VariableWriteRequest` | 用户在值表双击编辑数值 | `expression, value` | `MockDataNode` |

### 🟢 后端 → 前端（状态响应）

后端节点处理请求后发布，`ScopeNode` 订阅并更新 UI。

| 事件 | 发布时机 | 载荷字段 | 发布者 |
|------|----------|----------|--------|
| `ElfLoaded` | ELF 解析成功 | `path, variables, symbol_count, file_count, values` | `MockElfNode` |
| `ElfLoadFailed` | ELF 解析失败 | `path, reason` | `MockElfNode` |
| `ProbeScanResult` | 探针扫描完成 | `probes: [{name, vendor, uid, ...}]` | `MockProbeNode` |
| `ProbeConnected` | 探针连接成功 | `target_name, swd_freq_khz, probe_name` | `MockProbeNode` |
| `ProbeDisconnected` | 探针断开 | _(无)_ | `MockProbeNode` |
| `ProbeConnectionFailed` | 探针连接失败 | `reason` | `MockProbeNode` |
| `SampleData` | 每采到一个样本立即发布 | `buffers, timestamps` | `MockDataNode` |
| `SamplingStatus` | 采样启动/停止 | `is_running, sample_count, actual_rate` | `MockDataNode` |

---

## 事件字段详解

### `ElfLoaded`

ELF 文件解析完成后发布，**`values`** 字段携带从 ELF 数据段读取的运行时初始值，前端直接填入变量树「数值」列。

```python
class ElfLoaded(Event):
    path: str              # 文件路径
    variables: list = None  # Variable 对象列表（结构体展开后所有子成员）
    symbol_count: int = 0   # 符号总数
    file_count: int = 0     # 源文件数量
    values: dict = None     # { "变量路径": 初始值, ... }
```

### `SampleData`

精简后的实时数据事件。**每采到一个样本就立即发布一条**，每条 `SampleData` 携带一个时间戳和每个变量单个值（非列表）。

```python
class SampleData(Event):
    buffers: dict = None   # { path: value, ... }  — 单样本值（非列表）
    timestamps: list = None  # [float]              — 单时间戳
```

> **设计说明**：移除了 `active_paths`（从 `buffers.keys()` 获取）、`sample_rate`、`actual_rate`、`sample_count`。采样率/实际采样率通过 `SamplingStatus` 传递。每个事件仅包含**一个样本点**，前端 `extend` 追加到本地缓冲区。

### `VariableWriteRequest`

用户编辑变量值时发送，移除了冗余的 `address` 字段（前端从变量树 registry 自行管理）。

```python
class VariableWriteRequest(Event):
    expression: str = ""   # 变量表达式，如 "g_powerStats.current"
    value: float = 0.0      # 要写入的绝对值
```

### `StartSamplingRequest`

```python
class StartSamplingRequest(Event):
    variable_paths: list = None   # ["g_powerStats.voltage", ...]
    sample_rate_hz: int = 100     # 目标采样率 (Hz)
    buffer_seconds: int = 300     # 缓冲区时长 (秒)
```

### `SamplingStatus`

```python
class SamplingStatus(Event):
    is_running: bool = False      # 采样中？
    sample_count: int = 0         # 已采样本数
    actual_rate: float = 0.0      # 实际采样率 (Hz)
```

---

## 关键机制

### 1. 变量树：只有基础值节点才有复选框和数值

通过 `helpers.is_base_type(ti)` 判断类型是否为"基础值节点"（可勾选、可采样、可绘制、可写入）：

| 类型 | 可勾选？ | 显示数值？ | 可编辑值？ |
|------|---------|-----------|-----------|
| `BaseType`（`float`, `uint32_t`…） | ✅ | ✅ | ✅ |
| `EnumType` | ✅ | ✅ | ✅ |
| `PointerType(→BaseType)`（通过 `*` 表达式解引用） | ✅ | ✅ | ✅ |
| `StructType` | ❌ | ❌ | ❌ |
| `ArrayType`（数组本身） | ❌ | ❌ | ❌ |
| `PointerType(→Struct)` / `void*` | ❌ | ❌ | ❌ |

### 2. 变量写回：写入绝对值，不是偏移量

用户编辑值表后：
```
用户写 current = 2.0
  → MockDataNode._overrides["g_powerStats.current"] = 2.0
  → 后续采样：不走波形生成，直接返回 2.0
  → _recompute_dependencies() 级联更新 power = voltage × 2.0
```

### 3. 依赖变量：power = voltage × current

`MockDataNode` 内置依赖关系定义：

```python
self._dependencies = {
    "g_powerStats.power": (
        lambda v, i: v * i,                           # 计算函数
        ["g_powerStats.voltage", "g_powerStats.current"],  # 输入依赖
    ),
}
```

- 生成时：先生成所有独立变量 → 再用同一样本的独立值计算依赖变量
- 写入时：用户写 `voltage` 或 `current` → `_recompute_dependencies()` 自动更新 `power`

### 4. 采样发布：每采一个样本立即发布

```python
# MockDataNode._process()
for _ in range(to_generate):
    one_sample = {...}
    self.publish(SampleData(buffers=one_sample, timestamps=[t]))
```

不攒批、不延迟，每个样本独立发布，前端逐点追加到绘图缓冲区。

---

## 通信流程图

### 探针连接流程

```
用户点击「扫描」 →  ScopeNode 发布 ScanProbesRequest
                    →  MockProbeNode 接收，模拟扫描
                    →  MockProbeNode 发布 ProbeScanResult
                    →  ScopeNode 接收，更新探针下拉列表

用户选择探针 →  点击「连接」
              →  ScopeNode 发布 ConnectProbeRequest
              →  MockProbeNode 接收，建立连接
              →  MockProbeNode 发布 ProbeConnected
              →  ScopeNode 接收，指示灯变绿
```

### ELF 导入流程

```
用户点击「导入 ELF」 →  ScopeNode 发布 ImportElfRequest
                      →  MockElfNode 接收，解析 DWARF
                      →  MockElfNode 发布 ElfLoaded(path, variables, values)
                      →  ScopeNode 接收，填充变量树 + 初始值
```

### 采样流程

```
用户勾选变量 →  ScopeNode 记录 monitored_vars
用户点击「开始」 →  ScopeNode 发布 StartSamplingRequest
                  →  MockDataNode 启动采样循环
                  →  MockDataNode 每帧逐个生成样本
                  → 每采一个 → 发布 SampleData(buffers, timestamps)
                  →  ScopeNode 接收，extend 追加到缓冲区，更新波形
用户点击「停止」 →  ScopeNode 发布 StopSamplingRequest
                  →  MockDataNode 停止，发布 SamplingStatus(running=false)
                  →  ScopeNode 清空缓冲区，恢复按钮
```

### 变量写回流程 ⭐

```
用户双击值表「数值」列 →  输入新值（如 current = 2.0），按回车
                       →  ScopeNode 发布 VariableWriteRequest(expression, value)
                       →  MockDataNode._on_write()
                            ├── 记录 _overrides["current"] = 2.0（绝对值）
                            └── _recompute_dependencies()
                                  └── power = voltage(3.3) × current(2.0) = 6.6
                                      → _overrides["power"] = 6.6
                       →  下一帧：current 固定为 2.0，power 固定为 6.6
                       →  波形实时跳变
```

---

## 文件结构

```
Scope/
├── README.md                         ← 本文档
├── main.py                           ← 旧版示例入口
├── src/
│   ├── framework/                    ← 核心框架
│   │   ├── __init__.py
│   │   ├── container.py              ← Container (事件总线 + 节点管理)
│   │   ├── event.py                  ← Event 基类
│   │   └── node.py                   ← Node 基类
│   └── scope/                        ← 示波器模块
│       ├── __init__.py               ← 导出所有事件和类
│       ├── events.py                 ← 所有事件定义
│       ├── node.py                   ← ScopeNode（前端节点）
│       ├── window.py                 ← ScopeMainWindow（PySide6 UI）
│       └── helpers.py                ← 颜色、常量、格式化、is_base_type
└── tests/
    ├── test_scope.py                 ← 测试入口（组装容器运行）
    └── mock/
        ├── mock_elf_node.py          ← 模拟 ELF 加载（含 PowerStats）
        ├── mock_probe_node.py        ← 模拟探针管理
        └── mock_data_node.py         ← 模拟采样 + 依赖计算 + 变量写回
```

---

## 节点清单

| 节点 | 类名 | 文件 | 作用 |
|------|------|------|------|
| `ScopeNode` | `ScopeNode` | `src/scope/node.py` | 前端 UI（PySide6 窗口） |
| `MockElfNode` | `MockElfNode` | `tests/mock/mock_elf_node.py` | 模拟 ELF DWARF 解析 |
| `MockProbeNode` | `MockProbeNode` | `tests/mock/mock_probe_node.py` | 模拟探针扫描/连接 |
| `MockDataNode` | `MockDataNode` | `tests/mock/mock_data_node.py` | 模拟数据采集 + 依赖计算 + 变量写回 |

---

## 旧版项目参考

`old/` 目录存放老版本 LoopMaster 的完整源码。该版本的事件定义和架构不同，但**核心算法**仍值得参考：

| 模块 | 路径 | 说明 |
|------|------|------|
| DWARF 解析 | `old/src/core/collector.py` | 从 ELF 读取 DWARF 调试信息，解析符号表、类型信息 |
| 变量树构建 | `old/src/core/collector.py` | 结构体展开、数组展开、类型推导 |
| 数据采集调度 | `old/src/core/collector.py` | SWD 内存读取与连续采样 |
| CMSIS-Pack SVD | `old/src/core/collector.py` | SVD 文件解析与外设寄存器映射 |
| 后端存储模型 | `old/src/core/models.py` | 采样数据的内存管理与持久化 |
| Mock 后端 | `old/src/core/mem_backend.py` | 完整的内存仿真后端 |
| 类型定义 | `old/src/typedefs/` | `Variable`, `StructType`, `ArrayType` 等（新版直接复用） |

> 新版（`src/scope/`）采用事件驱动架构重写了前后端通信，但底层数据类型定义（`src/typedefs/`）和 ELF 解析逻辑均继承自 `old/`。

---

## 添加新事件 / 新节点

1. **定义事件** → 在 `src/scope/events.py` 中添加 `Event` 子类
2. **导出事件** → 在 `src/scope/__init__.py` 中添加导入和 `__all__`
3. **创建节点** → 继承 `Node`，`_init()` 中 subscribe，`_process()` 中 publish
4. **注册运行** → 在 `tests/test_scope.py` 中 `container.add_node(your_node)`

```python
class MyEvent(Event):
    data: str = ""

class MyNode(Node):
    def _init(self):
        self.subscribe(MyEvent, self._on_event)

    def _on_event(self, e: MyEvent):
        print(f"收到: {e.data}")
        self.publish(MyEvent(data="pong"))
```

---

## 附录：事件一览表（完整字段定义）

| 事件类 | 方向 | 所有字段 |
|--------|------|----------|
| `ImportElfRequest` | 🔴 → | `path` |
| `ScanProbesRequest` | 🔴 → | _(无)_ |
| `ConnectProbeRequest` | 🔴 → | `probe_index`, `mode`, `swd_freq_hz` |
| `DisconnectProbeRequest` | 🔴 → | _(无)_ |
| `StartSamplingRequest` | 🔴 → | `variable_paths`, `sample_rate_hz`, `buffer_seconds` |
| `StopSamplingRequest` | 🔴 → | _(无)_ |
| `VariableWriteRequest` | 🔴 → | `expression`, `value` |
| `ElfLoaded` | 🟢 ← | `path`, `variables`, `symbol_count`, `file_count`, `values` |
| `ElfLoadFailed` | 🟢 ← | `path`, `reason` |
| `ProbeScanResult` | 🟢 ← | `probes` |
| `ProbeConnected` | 🟢 ← | `target_name`, `swd_freq_khz`, `probe_name` |
| `ProbeDisconnected` | 🟢 ← | _(无)_ |
| `ProbeConnectionFailed` | 🟢 ← | `reason` |
| `SampleData` | 🟢 ← | `buffers`, `timestamps` |
| `SamplingStatus` | 🟢 ← | `is_running`, `sample_count`, `actual_rate` |