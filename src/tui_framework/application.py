"""Application Protocol — TUI 应用的标准化接口契约。

定义 TUI 应用的最小契约，支持 duck typing 和 ``isinstance()`` 运行时检查。
ChatUIAdapter 实现此接口，桥接新框架与现有 ChatUIConsumer。

使用方式：
    from tui_framework.application import Application
    from tui.consumer.adapter import ChatUIAdapter

    adapter = ChatUIAdapter(consumer)
    assert isinstance(adapter, Application)  # True
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .widgets.base import Widget
    from .events.event_types import DisplayEvent
    from .terminal.output_target import IOutputTarget


@runtime_checkable
class Application(Protocol):
    """TUI 应用最小契约 — duck typing 兼容。

    任何实现以下 4 个方法 + 1 个属性的对象均可作为 Application 使用。

    Attributes:
        output_target: 获取当前输出目标实例（IOutputTarget 协议）。
    """

    @property
    def output_target(self) -> "IOutputTarget":
        """获取输出目标实例。

        用于外部代码向 TUI 输出内容或查询终端宽度。
        """
        ...

    def run(self) -> None:
        """启动 TUI 应用。

        等价于 ChatUIConsumer.start()：
        - 订阅事件总线
        - 启动渲染线程
        - 注册为活跃消费者
        - 展示启动品牌屏
        """
        ...

    def stop(self) -> None:
        """停止 TUI 应用。

        等价于 ChatUIConsumer.stop()：
        - 取消所有事件订阅
        - 排空命令队列
        - 停止渲染引擎
        - 注销活跃消费者
        - 清理渲染状态和底部栏
        """
        ...

    def on_event(self, event: "DisplayEvent") -> None:
        """接收并分发显示事件到渲染管线。

        将 DisplayEvent 发布到事件总线，由内部 EventDispatcher
        转换为 RenderCommand 并入队渲染线程处理。

        Args:
            event: 显示事件实例（DisplayEvent 子类）。
        """
        ...

    def register_widget(self, widget: "Widget") -> None:
        """注册新框架 Widget 到渲染管线。

        将 tui_framework 中的 Widget 注入到 TUI 应用的渲染流程中，
        支持渐进式迁移——新旧组件共存。

        Args:
            widget: 框架 Widget 实例（如 Input / Button / Select 等）。
        """
        ...


__all__ = ["Application"]
