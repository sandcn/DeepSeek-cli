"""核心层端口接口 — 定义核心层与基础设施之间的抽象协议（纯抽象层）

核心层通过此模块访问模型调用、UI 显示、配置、持久化等功能，
不直接依赖 api、ui、chat_msgs、checkpoint 等具体实现模块。
基础设施层实现这些协议（适配器模式），实现依赖倒置。

【架构规范】此模块仅导出端口抽象接口（ABC），不导出任何具体实现类。
所有适配器实现（Default*Adapter, JsonFile*, Mock* 等）通过
src.core.adapters 包导入，确保依赖方向为：高层→抽象←实现。

端口清单:
- AsyncModelPort    — 异步模型调用（LLM）
- ConfigPort        — 配置管理
- CachePort         — 通用缓存
- StatsPort         — 统计收集
- PersistencePort   — 会话持久化
- CheckpointPort    — 任务断点
- DisplayPort       — 用户显示
- EventPort         — 事件总线
- OutputPort        — 文本输出
- InterruptPort     — 中断检查
- ToolRegistryPort  — 工具注册表
- PromptBuilderPort — 提示词构建
- RenderPort        — 渲染
- HttpClientPort    — HTTP 客户端
- ObservabilityPort — 可观测性（定义于 observability.py）
"""

from .config import ConfigPort
from .display import DisplayPort
from .events import EventPort
from .http import HttpClientPort
from .interrupt import InterruptPort
from .model import AsyncModelPort, ModelResult
from .output import OutputPort
from .persistence import PersistencePort, CheckpointPort
from .prompt_builder import PromptBuilderPort
from .render import RenderPort
from .stats import StatsPort
from .tool_registry import ToolRegistryPort
from .observability import ObservabilityPort

__all__ = [
    # 配置
    "ConfigPort",
    # 模型
    "AsyncModelPort", "ModelResult",
    # 统计
    "StatsPort",
    # 持久化
    "PersistencePort", "CheckpointPort",
    # UI
    "DisplayPort", "EventPort", "OutputPort",
    # HTTP
    "HttpClientPort",
    # 中断检查
    "InterruptPort",
    # 工具注册表
    "ToolRegistryPort",
    # 提示词构建
    "PromptBuilderPort",
    # 渲染
    "RenderPort",
    # 可观测性
    "ObservabilityPort",
]
