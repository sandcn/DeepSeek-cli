"""ChatUIAdapter — 将 ChatUIConsumer 适配为 Application 接口。

设计模式：适配器（Adapter）
- ChatUIConsumer（被适配者）→ Application（目标接口）
- 全屏模式默认使用终端输出，显式注入 InlineOutputTarget 时切换为 inline 模式

使用方式：
    from tui.consumer.consumer import ChatUIConsumer
    from tui.consumer.adapter import ChatUIAdapter

    consumer = ChatUIConsumer()
    adapter = ChatUIAdapter(consumer)
    adapter.run()        # 启动 ChatUI
    adapter.stop()       # 停止 ChatUI
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from src.tui_framework.application import Application
from src.tui_framework.widgets.base import Widget
from src.tui_framework.events.event_types import DisplayEvent
from src.tui_framework.terminal.output_target import IOutputTarget

if TYPE_CHECKING:
    from .consumer import ChatUIConsumer

_logger = logging.getLogger(__name__)


class _OutputAdapterBridge:
    """内部桥接：将 src/renderer/output.py 的 OutputAdapter 包装为 IOutputTarget。

    用于 ChatUIAdapter 默认全屏模式下，将现有 OutputAdapter
    （Rich Console 包装）暴露为 IOutputTarget 接口。

    ## 全屏模式帧管理

    全屏模式下帧渲染由 TerminalTarget 负责（DECSTBM + SCOSC/DECRC），
    此桥接不参与帧管理——render_frame/clear_last_lines 均为空操作。
    ChatUIConsumer 的渲染线程通过 TerminalTarget 直接操作终端帧缓冲，
    桥接仅提供基础的 write/write_line/terminal_width 能力供框架 Widget 使用。

    注意：此桥接仅提供 terminal_width + write/write_line 基础能力，
    render_frame/clear_last_lines 为空操作（全屏模式不使用 inline 语义）。
    """

    def __init__(self, output_adapter: Any) -> None:
        self._adapter = output_adapter

    @property
    def terminal_width(self) -> int:
        return self._adapter.width

    @property
    def supports_inline(self) -> bool:
        return False

    def write(self, text: str) -> None:
        from rich.text import Text
        self._adapter.write(Text.from_ansi(text))

    def write_line(self, text: str = "") -> None:
        from rich.text import Text
        self._adapter.write(Text.from_ansi(text + "\n"))

    def render_frame(self, lines: list[str], last_lines: int) -> int:
        """全屏模式：render_frame 为空操作。

        ChatUI 的帧渲染由 TerminalTarget 处理，此桥接不参与帧管理。
        """
        return 0

    def clear_last_lines(self, n: int) -> None:
        """全屏模式：clear_last_lines 为空操作。"""
        pass

    def flush(self) -> None:
        pass


class ChatUIAdapter:
    """ChatUIConsumer → Application 适配器。

    将现有的全屏 ChatUIConsumer 包装为标准化 Application 接口，
    支持渐进式迁移：
    - 新框架 Widget 可通过 register_widget() 注入到渲染管线
    - on_event() 将 DisplayEvent 路由到 ChatUI 的渲染线程
    - output_target 默认桥接现有 OutputAdapter，可注入 InlineOutputTarget

    ## 约束
    - [安全] 不得破坏现有 ChatUI 功能
    - 默认全屏模式，仅显式注入 InlineOutputTarget 时切换为 inline 模式
    """

    def __init__(
        self,
        consumer: "ChatUIConsumer",
        output_target: IOutputTarget | None = None,
    ) -> None:
        """初始化适配器。

        Args:
            consumer: ChatUIConsumer 实例（被适配者）。
            output_target: 可选的 IOutputTarget 实现。
                           为 None 时默认桥接 consumer.output_adapter（全屏模式）。
                           传入 InlineOutputTarget 时切换为 inline 模式。
        """
        self._consumer = consumer
        self._widgets: list[Widget] = []

        # 设置 output_target：优先使用显式注入，否则桥接 OutputAdapter
        if output_target is not None:
            self._output_target = output_target
        else:
            self._output_target = _OutputAdapterBridge(consumer.output_adapter)

    @property
    def output_target(self) -> IOutputTarget:
        """获取当前输出目标。

        Returns:
            显式注入的 IOutputTarget 或 _OutputAdapterBridge（全屏模式）。
        """
        return self._output_target

    @property
    def consumer(self) -> "ChatUIConsumer":
        """获取被适配的 ChatUIConsumer 实例。"""
        return self._consumer

    @property
    def widgets(self) -> list[Widget]:
        """获取已注册的新框架 Widget 列表。"""
        return list(self._widgets)

    # ── Application 接口 ────────────────────────────────

    def run(self) -> None:
        """启动 ChatUI 应用。

        委托 ChatUIConsumer.start()，包括：
        - 订阅事件总线
        - 启动渲染线程
        - 注册为活跃消费者
        """
        self._consumer.start()

    def stop(self) -> None:
        """停止 ChatUI 应用。

        委托 ChatUIConsumer.stop()，包括：
        - 取消事件订阅
        - 停止渲染引擎
        - 清理渲染状态
        """
        self._consumer.stop()

    def on_event(self, event: DisplayEvent) -> None:
        """接收 DisplayEvent 并分发到 ChatUI 渲染管线。

        将事件发布到 DisplayEventBus，ChatUIConsumer 的 EventDispatcher
        会将事件转换为 RenderCommand 并入队渲染线程处理。

        Args:
            event: DisplayEvent 子类实例（KeyPressEvent/MouseEvent 等）。
        """
        self._consumer._bus.publish(event)

    def register_widget(self, widget: Widget) -> None:
        """注册新框架 Widget 到 Adapter。

        将 tui_framework 中的 Widget 注册到适配器，并集成到渲染管线：
        - consumer 未启动时：仅存储，等 run() 后可通过 iterate_widgets() 遍历
        - consumer 已启动时：通过 engine.push_cmd 将 widget 的 render() 输出
          写入 output_target

        Args:
            widget: 框架 Widget 实例（如 Input / Button / Select 等）。

        Raises:
            TypeError: widget 不是 Widget 实例。
        """
        if not isinstance(widget, Widget):
            raise TypeError(
                f"register_widget() 需要 Widget 实例，收到 {type(widget).__name__}"
            )
        self._widgets.append(widget)
        _logger.debug("已注册 Widget: id=%s type=%s", widget.widget_id, type(widget).__name__)

        # 若 consumer 已启动，将 widget 渲染输出通过 engine 管线注入
        try:
            if hasattr(self._consumer, '_engine') and self._consumer._started:
                rendered = widget.render()
                if rendered:
                    self._output_target.write_line(rendered)
        except Exception:
            _logger.debug(
                "Widget 渲染注入失败（管线未就绪）: id=%s", widget.widget_id, exc_info=True
            )

    def iterate_widgets(self):
        """遍历所有已注册的 Widget。

        生成器方法，按注册顺序逐个产出 Widget 实例。
        可在 run() 之后调用，用于遍历并手动操作已注册控件。

        Yields:
            Widget: 已注册的框架 Widget 实例。
        """
        yield from self._widgets

    def render_widgets(self) -> None:
        """渲染所有已注册的 Widget 到 output_target。

        按注册顺序调用每个 Widget 的 render()，将输出写入
        output_target.write_line()。consumer 未启动时仅记录日志。
        """
        if not self._widgets:
            return
        for widget in self._widgets:
            try:
                rendered = widget.render()
                if rendered:
                    self._output_target.write_line(rendered)
            except Exception:
                _logger.exception(
                    "render_widgets: Widget 渲染失败 id=%s", widget.widget_id
                )

    def unregister_widget(self, widget: Widget) -> None:
        """注销已注册的 Widget。

        Args:
            widget: 要移除的 Widget 实例。
        """
        try:
            self._widgets.remove(widget)
            _logger.debug("已注销 Widget: id=%s", widget.widget_id)
        except ValueError:
            _logger.debug("Widget 未注册，跳过注销: id=%s", widget.widget_id)

    # ── isinstance 兼容 ──────────────────────────────────

    @classmethod
    def __subclasshook__(cls, subclass: type) -> bool:
        """支持 isinstance(x, ChatUIAdapter) 运行时检查。

        通过 Application Protocol 的 @runtime_checkable 实现。
        """
        return NotImplemented


__all__ = ["ChatUIAdapter", "_OutputAdapterBridge"]
