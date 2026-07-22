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
from typing import Optional

from ..widget_base import Widget
from ..render_buffer import RenderBuffer
from ..animation.animator import AnimatorContext, BreathPalette
from ..core.text_utils import build_glow_ansi
from ..terminal.terminal import is_narrow

from .bottom_bar.theme import (
    _COLOR_ACCENT,
    _COLOR_DIM,
    _COLOR_RESET,
    _COLOR_SPEED,
    _COLOR_TIME,
    _COLOR_TOKEN,
    _COLOR_TOOL_FAIL,
    _COLOR_TOOL_OK,
)

_logger = logging.getLogger(__name__)


# ── Token 速度快照惰性加载 ──────────────────────────────
_TOKEN_SPEED_SNAPSHOT: Optional[callable] = None


def _get_snapshot():
    """获取 get_token_speed_snapshot 函数引用（惰性加载，异常静默）。"""
    global _TOKEN_SPEED_SNAPSHOT
    if _TOKEN_SPEED_SNAPSHOT is None:
        try:
            from ...api.stats import get_token_speed_snapshot
            _TOKEN_SPEED_SNAPSHOT = get_token_speed_snapshot
        except ImportError:
            _TOKEN_SPEED_SNAPSHOT = False
    return _TOKEN_SPEED_SNAPSHOT if callable(_TOKEN_SPEED_SNAPSHOT) else None


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

        Args:
            model_name: 模型名。
            tool_count: 运行中的工具数。
            tool_fail_count: 失败工具数。
            tool_total: 累计工具总数。
            status_active: 是否流式输出活跃。

        Returns:
            格式化后的状态行字符串（含 ANSI 色号）。
        """
        # ── 模型名字（始终显示，带 · 图标） ──
        if model_name:
            if status_active:
                ctx = AnimatorContext.get_default()
                _pulse_frame = ctx.breath_frame
                if _pulse_frame > 0:
                    _pulse_color = ctx.sine_color(36, 45, 4)
                else:
                    _pulse_color = BreathPalette.get_color("status_pulse", _pulse_frame)
                model_part = (
                    f"\033[38;5;{_pulse_color}m\u00b7\033[0m"
                    f" {_COLOR_ACCENT}{model_name}{_COLOR_RESET}"
                )
            else:
                model_part = (
                    f"{_COLOR_ACCENT}\u00b7{_COLOR_RESET}"
                    f" {_COLOR_ACCENT}{model_name}{_COLOR_RESET}"
                )
        else:
            model_part = ""

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

        # 工具调用计数（带 · 图标，运行中/总数格式）
        if tool_total > 0:
            if not is_narrow():
                _gear_frame = AnimatorContext.get_default().frame
                glow_gear = f"{build_glow_ansi(_gear_frame, 45, 12)}\u00b7\033[0m "
            else:
                glow_gear = ""
            if tool_count > 0:
                # 运行中格式：· <运行中>→<总数>
                if tool_fail_count > 0:
                    total_colored = f"{_COLOR_TOOL_FAIL}{tool_total}{_COLOR_RESET}"
                else:
                    total_colored = f"{_COLOR_TOOL_OK}{tool_total}{_COLOR_RESET}"
                parts.append(
                    f"{glow_gear}"
                    f"{_COLOR_ACCENT}{tool_count}{_COLOR_RESET}"
                    f"{_COLOR_DIM}→{_COLOR_RESET}"
                    f"{total_colored}"
                )
            else:
                # 无运行工具时保持原有逻辑
                done = tool_total - tool_fail_count
                if tool_fail_count > 0:
                    parts.append(
                        f"{glow_gear}"
                        f"{_COLOR_TOOL_OK}{done}{_COLOR_RESET}"
                        f"{_COLOR_DIM}/{_COLOR_RESET}"
                        f"{_COLOR_TOOL_FAIL}{tool_total}{_COLOR_RESET}"
                    )
                else:
                    parts.append(f"{glow_gear}{_COLOR_TOOL_OK}{tool_total}{_COLOR_RESET}")

        # 耗时（蓝灰高亮）
        if elapsed > 0:
            if elapsed >= 60:
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                dur = f"{mins}:{secs:02d}" if mins < 60 else f"{mins // 60}:{mins % 60:02d}:{secs:02d}"
            else:
                dur = f"{elapsed:.1f}s"
            parts.append(f"{_COLOR_TIME}{dur}{_COLOR_RESET}")

        # 令牌数（靛蓝色）
        if total > 0:
            tok_str = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
            parts.append(f"{_COLOR_TOKEN}{tok_str}t{_COLOR_RESET}")

        # 实时 token 速度（tok/s，琥珀色）
        if per_second_speed > 0:
            if per_second_speed >= 1:
                speed_str = f"{per_second_speed:.1f}"
            else:
                speed_str = f"{per_second_speed:.2f}"
            parts.append(f"{_COLOR_SPEED}{speed_str}t/s{_COLOR_RESET}")

        sep = f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        status = sep.join(parts) if parts else ""
        if status and not is_narrow():
            _deco_frame = AnimatorContext.get_default().frame
            glow_dot = f"{build_glow_ansi(_deco_frame, 45, 12)}\u00b7\033[0m"
            status = f"{status}  {glow_dot}"
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
