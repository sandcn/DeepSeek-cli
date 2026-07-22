"""框架渲染器基类 — FrameworkRenderer。

提供 ComponentRegistry 驱动的命令分发机制和框架级渲染命令处理方法。
与聊天域无关，可被任何 TUI 应用独立复用。

架构分层（2026-07-22 泛化）：
  FrameworkRenderer     — 框架通用基类：render() 分发 + 框架级 _do_* 方法
  TuiRenderer           — 聊天域子类（renderer.py）：聊天域 _do_* 方法

用法：
  from src.tui.engine.renderer_base import FrameworkRenderer, register_render_command

  class MyRenderer(FrameworkRenderer):
      @register_render_command(MyCommand.XXX, (1,))
      def _do_xxx(self, text: str) -> None: ...
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter
    from .protocols import BottomBarProtocol

from .const import RenderCommand
from ..core.component_registry import ComponentRegistry
from ..components import (
    NotificationBlock,
    ErrorBlock,
    WriteLineBlock,
    SplashScreen,
)

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 渲染命令注册装饰器
# ═══════════════════════════════════════════════════════════

def register_render_command(command_id: int, arg_indices: tuple[int, ...] = ()) -> Callable:
    """装饰器工厂：在 _do_* 方法上设置声明式标记属性。

    用法: 在 _do_* 方法上使用 @register_render_command(RenderCommand.XXX, (i,))，
    装饰时仅设置 method._render_command_id = (command_id, arg_indices) 属性，
    不再调用 ComponentRegistry.register()——注册由 ComponentRegistry.__init__
    在 _populate_defaults() 中统一负责，确保 reset_default() 后命令不丢失。
    """
    def decorator(method: Callable) -> Callable:
        method._render_command_id = (command_id, arg_indices)  # type: ignore[attr-defined]
        return method
    return decorator


# ═══════════════════════════════════════════════════════════
# FrameworkRenderer — 框架通用渲染器基类
# ═══════════════════════════════════════════════════════════

class FrameworkRenderer:
    """框架通用渲染器基类 — 执行框架级 RenderCommand 并直接输出。

    通过 ComponentRegistry 将命令 ID 映射到 _do_* 方法，支持子类化扩展。
    框架级命令（NOTIFICATION/WRITE_LINE/ERROR/SPLASH/SUBAGENT_FRAME）
    在此处理；聊天域命令由子类 TuiRenderer 处理。
    """

    def __init__(
        self,
        output_adapter: "OutputAdapter",
        cursor_tracker: Any = None,
        bottom_bar: "BottomBarProtocol | None" = None,
    ):
        self._adapter = output_adapter
        self._tracker = cursor_tracker
        self._bb = bottom_bar

    @property
    def output_adapter(self) -> "OutputAdapter":
        """获取当前 OutputAdapter 实例。"""
        return self._adapter

    def _record_lines(self, n: int) -> None:
        """记录渲染输出的行数到光标追踪器。

        Args:
            n: 新增的行数。
        """
        if self._tracker is not None:
            self._tracker.record_newlines(n)

    def render(self, cmd: tuple) -> None:
        """分发渲染命令到对应的 _do_* 方法。

        通过 ComponentRegistry.resolve() 将命令 ID 映射到方法名和参数索引，
        提取参数后调用对应处理方法。

        Args:
            cmd: 渲染命令元组，格式为 (command_id, *args)
        """
        if not cmd:
            return
        cid = cmd[0]
        entry = ComponentRegistry.get_default().resolve(cid)
        if entry is None:
            from .utils import _cmd_name
            _logger.error("未知渲染命令: %s", _cmd_name(cid))
            return
        method_name, arg_indices = entry
        method = getattr(self, method_name)
        args = tuple(cmd[i] for i in arg_indices)
        method(*args)

    # ═══════════════════════════════════════════════════════
    # 框架级渲染命令处理方法
    # ═══════════════════════════════════════════════════════

    @register_render_command(RenderCommand.NOTIFICATION, (1,))
    def _do_notification(self, text: str) -> None:
        """渲染通用通知消息。"""
        block = NotificationBlock(text)
        self._record_lines(block.render_to_adapter(self._adapter))

    @register_render_command(RenderCommand.WRITE_LINE, (1,))
    def _do_write_line(self, text: str) -> None:
        """直接写入一行文本到终端。"""
        block = WriteLineBlock(text)
        self._record_lines(block.render_to_adapter(self._adapter))

    @register_render_command(RenderCommand.ERROR, (1,))
    def _do_error(self, message: str) -> None:
        """渲染系统错误消息（红色 ! 样式）。"""
        block = ErrorBlock(message)
        self._record_lines(block.render_to_adapter(self._adapter))

    @register_render_command(RenderCommand.SPLASH, ())
    def _do_splash(self) -> None:
        """渲染启动品牌屏（仅首次展示一次）。

        从 bottom_bar 获取已设置的模型名（若有），否则 SplashScreen 自行从 config 读取。
        """
        # 临时桥接：_bb 的 model_name 当前为私有属性 _model_name，优先尝试公开属性名
        model_name = getattr(self._bb, 'model_name', getattr(self._bb, '_model_name', '')) if self._bb is not None else ''
        splash = SplashScreen(model_name=model_name)
        self._record_lines(splash.render_to_adapter(self._adapter))

    @register_render_command(RenderCommand.SUBAGENT_FRAME, (1,))
    def _do_subagent_frame(self, frame_lines: tuple) -> None:
        """将 subagent 面板行数据传递给 BottomBar 渲染。

        不再直接写 ANSI 到上屏，改为委托 BottomBar.force_redraw()
        在固定下屏区域渲染。
        """
        if not frame_lines:
            return
        if len(frame_lines) < 4:
            return
        lines = frame_lines[0]
        if not lines or not isinstance(lines, (list, tuple)):
            return
        if self._bb is not None and hasattr(self._bb, 'set_subagent_frame'):
            self._bb.set_subagent_frame(list(lines))
