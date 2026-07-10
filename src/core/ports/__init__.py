"""核心层端口接口 — 定义核心层与基础设施之间的抽象协议

核心层通过此模块访问模型调用、UI 显示、配置、持久化等功能，
不直接依赖 api、ui、chat_msgs、checkpoint 等具体实现模块。
基础设施层实现这些协议（适配器模式），实现依赖倒置。

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
"""

from .http import HttpClientPort, DefaultHttpClientAdapter
from .config import ConfigPort, DefaultConfigAdapter, MockConfigAdapter
from .display import DisplayPort
from .events import EventPort
from .interrupt import InterruptPort
from .model import AsyncModelPort, DefaultAsyncModelAdapter, MockAsyncModelAdapter, ModelResult
from .output import OutputPort
from .persistence import PersistencePort, CheckpointPort, JsonFilePersistence, JsonFileCheckpoint
from .tool_registry import ToolRegistryPort
from .prompt_builder import PromptBuilderPort, DefaultPromptBuilderAdapter
from .render import RenderPort, DefaultRenderAdapter, NullRenderAdapter
from .stats import StatsPort, DefaultStatsAdapter, MockStatsAdapter
from ..cache import CachePort, LRUCache, NullCache

__all__ = [
    # 配置
    "ConfigPort", "DefaultConfigAdapter", "MockConfigAdapter",
    # 模型
    "AsyncModelPort", "DefaultAsyncModelAdapter", "MockAsyncModelAdapter", "ModelResult",
    # 缓存
    "CachePort", "LRUCache", "NullCache",
    # 统计
    "StatsPort", "DefaultStatsAdapter", "MockStatsAdapter",
    # 持久化
    "PersistencePort", "CheckpointPort", "JsonFilePersistence", "JsonFileCheckpoint",
    # UI
    "DisplayPort", "EventPort", "OutputPort",
    # HTTP
    "HttpClientPort", "DefaultHttpClientAdapter",
    # 中断检查
    "InterruptPort",
    # 工具注册表
    "ToolRegistryPort",
    # 提示词构建
    "PromptBuilderPort", "DefaultPromptBuilderAdapter",
    # 渲染
    "RenderPort", "DefaultRenderAdapter", "NullRenderAdapter",
]
