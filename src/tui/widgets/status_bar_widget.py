"""StatusBarWidget — 状态行 Widget 组件。

将 _StatusMixin 的状态行格式化逻辑封装为 Widget 子类。
接受 props 驱动渲染到 RenderBuffer，无内部 I/O。

设计模式：
  - Widget 模板方法：props 驱动，render(buffer) 渲染
  - 无副作用：纯函数式渲染，不涉及终端操作

使用示例:
    from src.tui.widgets.status_bar_widget import StatusBarWidget
    from src.tui.render_buffer import RenderBuffer

    sb = StatusBarWidget(props={
        "model_name": "deepseek-v4",
        "tool_count": 3,
        "tool_fail_count": 1,
        "status_active": True,
    })
    buf = RenderBuffer(80, 1)
    sb.render(buf)
    print(buf.render())
"""

from __future__ import annotations

import logging

from ..widget_base import Widget
from ..render_buffer import RenderBuffer
from ..animation.animator import AnimatorContext
from ..terminal.terminal import is_narrow

from ._snapshot import _get_snapshot
from ._status_format import (
    build_model_label,
    build_tool_count_text,
    build_elapsed_text,
    build_token_text,
    build_speed_text,
    build_glow_deco,
)
from .bottom_bar.theme import _COLOR_DIM, _COLOR_RESET

_logger = logging.getLogger(__name__)


__all__ = [
    "StatusBarWidget",
]


class StatusBarWidget(Widget):
    """状态行 Widget — 渲染模型名/耗时/令牌数/工具计数的状态信息行。

    Props:
        model_name (str): 当前模型名。
        tool_count (int): 当前运行中的工具数。
        tool_fail_count (int): 失败工具数。
        tool_total (int): 累计工具总数。
        status_active (bool): 是否处于流式输出活跃态。
        main_phase (str): 主Agent阶段（"thinking"/"answering"/"parsing" 等）。
    """

    def __init__(self, props: dict | None = None) -> None:
        """初始化状态行 Widget。

        Args:
            props: 属性字典，支持 model_name/tool_count/tool_fail_count/
                   tool_total/status_active/main_phase 等属性。
        """
        merged = dict(props) if props else {}
        merged.setdefault("model_name", "")
        merged.setdefault("tool_count", 0)
        merged.setdefault("tool_fail_count", 0)
        merged.setdefault("tool_total", 0)
        merged.setdefault("status_active", False)
        merged.setdefault("main_phase", "")
        super().__init__(props=merged)

    def render(self, buffer: RenderBuffer) -> None:
        """渲染状态行到 RenderBuffer。

        从 props 读取模型名/工具计数/令牌速度等数据，
        格式化为多色分层的状态行文本后写入 buffer。

        Args:
            buffer: 目标 RenderBuffer 实例。
        """
        # 优先从 state 读取（set_status 更新的值），以 props 为默认值
        model_name = self._state.get("model_name") or self._props.get("model_name", "")
        tool_count = self._state.get("tool_count") or self._props.get("tool_count", 0)
        tool_fail_count = self._state.get("tool_fail_count") or self._props.get("tool_fail_count", 0)
        tool_total = self._state.get("tool_total") or self._props.get("tool_total", 0)
        status_active = self._state.get("status_active") if "status_active" in self._state else self._props.get("status_active", False)

        # 构建状态行文本（与 _StatusMixin._format_status 相同逻辑）
        status_line = self._build_status_text(
            model_name, tool_count, tool_fail_count,
            tool_total, status_active,
        )
        if status_line:
            buffer.write(0, 0, status_line)

    def _build_status_text(
        self,
        model_name: str,
        tool_count: int,
        tool_fail_count: int,
        tool_total: int,
        status_active: bool,
    ) -> str:
        """构建状态行文本（优雅信息风）。

        委托 ``_status_format`` 共享纯函数实现格式化。
        本方法负责：数据获取（snapshot）、窄屏条件判断、各部件组装。

        Args:
            model_name: 模型名。
            tool_count: 运行中的工具数。
            tool_fail_count: 失败工具数。
            tool_total: 累计工具总数。
            status_active: 是否流式输出活跃。

        Returns:
            格式化后的状态行字符串（含 ANSI 色号）。
        """
        ctx = AnimatorContext.get_default()
        frame = ctx.frame

        # ── 模型名字（始终显示，带 · 图标，委托共享函数） ──
        model_part = build_model_label(model_name, status_active, frame) if model_name else ""

        # ★ 非流式活跃时仅显示模型名
        if not status_active:
            return model_part

        snap_func = _get_snapshot()
        if snap_func is None:
            return model_part

        try:
            snap = snap_func()
        except Exception:
            return model_part

        total = snap.get("total_tokens", 0)
        elapsed = snap.get("elapsed_seconds", 0.0)
        per_second_speed = snap.get("per_second_speed", 0.0)

        if total <= 0 and elapsed <= 0 and per_second_speed <= 0 and tool_total <= 0:
            return model_part

        parts = []

        # 工具调用计数（委托共享函数）
        tool_text = build_tool_count_text(tool_count, tool_total, tool_fail_count, frame)
        if tool_text:
            parts.append(tool_text)

        # 耗时（委托共享函数）
        elapsed_text = build_elapsed_text(elapsed)
        if elapsed_text:
            parts.append(elapsed_text)

        # 令牌数（委托共享函数）
        token_text = build_token_text(total)
        if token_text:
            parts.append(token_text)

        # 实时 token 速度（委托共享函数，仅 speed>0 时显示）
        if per_second_speed > 0:
            speed_text = build_speed_text(per_second_speed)
            if speed_text:
                parts.append(speed_text)

        sep = f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        status = sep.join(parts) if parts else ""
        if status and not is_narrow():
            status = f"{status}  {build_glow_deco(frame)}"
        if model_part and status:
            return f"{model_part}  {status}"
        return model_part or status

    def set_status(self, **kwargs) -> None:
        """批量更新状态属性（通过 set_state 触发重渲染）。

        遵循 Widget 基类契约：仅更新 ``_state``，不直接突变 ``_props``。
        ``render()`` 优先从 ``_state`` 读取，以 ``_props`` 为默认值兜底。

        Args:
            **kwargs: 要更新的状态属性，如 model_name="gpt-4", tool_count=5
        """
        self.set_state(kwargs)

    def __repr__(self) -> str:
        return (
            f"StatusBarWidget(model={self._props.get('model_name')!r}, "
            f"tools={self._props.get('tool_total', 0)})"
        )
