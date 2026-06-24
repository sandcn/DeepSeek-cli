"""默认适配器工厂 — 为 Agent 提供默认端口实现（不暴露为公开 API）

这些工厂函数封装了对 ui.adapters 的依赖，
确保 core/agent.py 不直接引用 ui. 命名空间。

所有 ui 导入均为惰性导入（在函数体内延迟加载），
避免 core/ports 模块加载时触发 ui 包的初始化。
"""

from __future__ import annotations


def create_default_display_port():
    """创建默认 DisplayPort 实现。

    返回 UIDisplayAdapter 包装的 EventBusDisplayProxy 实例。
    EventBusDisplayProxy 将所有方法调用转为 EventBus 事件发布，
    UIDisplayAdapter 作为门面委托给 EventBusDisplayProxy。
    """
    from ...ui.adapters import UIDisplayAdapter
    from ...ui.events.adapters import EventBusDisplayProxy
    _real_display = EventBusDisplayProxy(source="agent")
    return UIDisplayAdapter(_real_display)


def create_default_event_port():
    """创建默认 EventPort 实现。

    返回 UIEventAdapter 实例，委托给 ui.events.event_bus.DisplayEventBus。
    """
    from ...ui.adapters import UIEventAdapter
    return UIEventAdapter()


def create_default_output_port():
    """创建默认 OutputPort 实现。

    返回 UIOutputAdapter 实例，委托给 ui.events.publish_output。
    """
    from ...ui.adapters import UIOutputAdapter
    return UIOutputAdapter()
