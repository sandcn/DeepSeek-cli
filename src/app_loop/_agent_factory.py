"""单次模式 Agent 工厂 — 独立模块（打破 app_loop 循环依赖）

从 ``_single.py`` 提取 ``_make_event_agent``：``_session_setup`` 与 ``_single``
均可依赖本模块，消除 ``_session_setup ↔ _single`` 循环。

「一切皆插件」：优先经内核 ``ctx.agent_loop`` 服务组装事件化 Agent
（工具注册表 / 模型端口 / 配置端口 / 提示词端口 / 显示端口均由插件提供）；
内核缺失时（单元测试、独立调用）回退既有默认端口构造。
"""

from __future__ import annotations

from ..core.agent import Agent


def _make_event_agent(model=None):
    """创建通过 EventBus 发布事件的 Agent 实例（内核优先，回退默认）。"""
    from ..core.adapters.kernel_runtime import active_agent_factory

    factory = active_agent_factory()
    if factory is not None:
        return factory(model)

    from ..core.adapters.display import DefaultDisplayAdapter
    from ..core.adapters.events import DisplayEventBusAdapter
    from ..core.adapters.output import DefaultOutputAdapter

    return Agent(
        model=model,
        display_port=DefaultDisplayAdapter(source="agent"),
        event_port=DisplayEventBusAdapter(source="agent"),
        output_port=DefaultOutputAdapter(),
    )


__all__ = ["_make_event_agent"]
