"""Agent DI — 默认端口适配器工厂函数。

从 agent.py 提取，提供 _create_default_ports() 和 _resolve_port()，
减少 agent.py 的模块加载副作用和文件行数。
"""

from __future__ import annotations

from ..core.adapters.interrupt import DefaultInterruptAdapter


# ── 默认适配器工厂 ────────────────────────────────────────
# 将 __init__ 中重复的 "if X is None: from ... import DefaultX; self._x = DefaultX()" 模式
# 提取为工厂函数，延迟导入各适配器类以减少模块加载副作用。

def _create_default_ports():
    """创建默认端口适配器字典（方法体内延迟导入）。"""
    from ..core.adapters.model import DefaultAsyncModelAdapter
    from ..core.adapters.config import DefaultConfigAdapter
    from ..core.adapters.observability import DefaultObservabilityAdapter
    from ..core.adapters.prompt_builder import DefaultPromptBuilderAdapter
    from ..core.adapters.display import DefaultDisplayAdapter
    from ..core.adapters.events import DisplayEventBusAdapter
    from ..core.adapters.output import DefaultOutputAdapter
    return {
        "async_model": DefaultAsyncModelAdapter(),
        "config": DefaultConfigAdapter(),
        "observability": DefaultObservabilityAdapter(),
        "prompt_builder": DefaultPromptBuilderAdapter(),
        "display": DefaultDisplayAdapter(source="agent"),
        "events": DisplayEventBusAdapter(source="agent"),
        "output": DefaultOutputAdapter(),
        "interrupt": DefaultInterruptAdapter(),
    }


def _resolve_port(value, defaults_dict, key):
    """辅助：value 不为 None 则返回 value，否则返回 defaults_dict[key]"""
    return value if value is not None else defaults_dict[key]
