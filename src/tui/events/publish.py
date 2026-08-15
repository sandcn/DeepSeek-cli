"""事件发布统一门面 — 收敛 DisplayEventBus 单例散点调用。

背景（2026-08-16 架构改进方向 B）：api/tools/core 等层存在多处
``DisplayEventBus.get_default().publish(...)`` 散点调用——高层模块直接
耦合进程级单例，无法在测试/多实例场景注入独立总线。

本模块收敛**发布路径**为类型化 ``emit()``：
  - ``emit(event)``             — 发布到默认总线（行为与旧散点调用完全一致）；
  - ``emit(event, bus=...)``    — 显式注入总线（测试/多实例场景）；
  - ``default_bus()``           — 默认总线访问器（惰性获取，不产生导入副作用）。

★ 架构改进方向 D（2026-08-16）：``DisplayEventBus`` 已支持直接构造独立
实例（不再强制单例）——``emit(event, bus=独立实例)`` 即跨总线发布。默认
总线（``get_default()``）保留为进程级共享实例（CLI/WebUI 既有架构约束，
webui bridge 依赖默认实例转发事件）。

约定：各层**发布**事件统一经本模块 ``emit``；订阅/读取总线仍可经
``DisplayEventBus.get_default()``（本模块不改变既有订阅语义）。发布路径
收敛后，多实例场景只需注入独立总线，无需改动全部调用方。

设计约束：
  - 模块级零依赖（bus 惰性 import，避免事件门面引入循环依赖）；
  - ``emit`` 不做异常吞没——发布异常由 EventBus.publish 自身隔离
    （handler 异常已在总线内限频记录），调用方保持原有语义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    # 仅供类型检查（运行时注解均为字符串形式，不求值）
    from .event_bus import DisplayEventBus
    from .event_types import DisplayEvent


def default_bus() -> "DisplayEventBus":
    """获取默认发布总线（进程级单例，惰性获取）。

    首次调用时经 ``DisplayEventBus.get_default()`` 获取并缓存于模块级——
    惰性语义保证：TUI 事件模块仅被引用而不发布时，不强制初始化单例。
    """
    from .event_bus import DisplayEventBus
    return DisplayEventBus.get_default()


def emit(event: "DisplayEvent", *, bus: "Optional[DisplayEventBus]" = None) -> None:
    """类型化发布显示事件（统一发布入口）。

    Args:
        event: DisplayEvent 实例（frozen dataclass）。
        bus: 显式注入的事件总线；None 时使用默认总线
            （``default_bus()``，进程级单例）。

    Notes:
        bus 参数为方向 D（单例解耦）预留——测试/多实例场景经此注入
        独立总线，生产路径省略走默认单例（行为与旧调用完全一致）。
    """
    target = bus if bus is not None else default_bus()
    target.publish(event)


__all__ = ["emit", "default_bus"]
