"""会话工厂 — 应用层创建 ChatSession 的统一入口（内核优先）。

「一切皆插件」：ChatSession 由内核 ``ctx.sessions`` 插件服务创建（会话
持久化 / checkpoint / 配置端口均由插件与适配器提供）；内核缺失时（单元
测试、独立调用）回退直接构造 ``ChatSession`` 并 ``initialize()``。

``event_agent=True`` 时使用事件化 Agent（显示 / 工具调用经 DisplayEventBus
渲染到 TUI），等价于既有 ``ChatSession(agent=_make_event_agent())`` 语义。
"""

from __future__ import annotations

from typing import Any, Optional

from ..core.session import ChatSession
from ._agent_factory import _make_event_agent


def create_session(
    *,
    agent: Any = None,
    model: Optional[str] = None,
    event_agent: bool = False,
    **kwargs,
) -> ChatSession:
    """创建并初始化 ChatSession（内核 sessions 服务优先，回退直接构造）。

    Args:
        agent: 显式 Agent 实例（优先于 event_agent）；
        model: 模型名（覆盖配置默认值）；
        event_agent: 未显式传 agent 时，使用事件化 Agent；
        **kwargs: 透传 ChatSession 构造参数（persistence_port / checkpoint_port /
                  config_port / sandbox / observability_port ...）。
    """
    if agent is None and event_agent:
        agent = _make_event_agent(model)

    from ..core.adapters.kernel_runtime import active_session_factory

    factory = active_session_factory()
    if factory is not None:
        return factory(agent=agent, model=model, **kwargs)

    session = ChatSession(agent=agent, model=model, **kwargs)
    session.initialize()
    return session


__all__ = ["create_session"]
