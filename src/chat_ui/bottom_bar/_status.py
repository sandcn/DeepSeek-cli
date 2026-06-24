"""底部栏状态渲染器 — 状态行格式化、工具计数、token 速度快照。

从 _StatusMixin（原 ui/_bottom_bar_status.py）重构为独立类 StatusRenderer，
不再依赖宿主类属性，状态字段通过 __init__ 显式注入。

使用方式：
    renderer = StatusRenderer(model_name="gpt-4", tool_total=3)
    line = renderer.format_status()                     # 使用当前实例状态
    line = renderer.format_status(tool_total=5)         # 覆盖指定字段
"""

from __future__ import annotations

import logging
from typing import Optional

from ._theme import (
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


# ── 模块级 get_token_speed_snapshot 缓存（避免内部方法每次 import） ──
_TOKEN_SPEED_SNAPSHOT: Optional[callable] = None  # 也可赋值为 False（标记不可用）


def _get_snapshot():
    """获取 get_token_speed_snapshot 函数引用（惰性加载，异常静默）。"""
    global _TOKEN_SPEED_SNAPSHOT
    if _TOKEN_SPEED_SNAPSHOT is None:
        try:
            from ...api.stats import get_token_speed_snapshot
            _TOKEN_SPEED_SNAPSHOT = get_token_speed_snapshot
        except ImportError:
            _TOKEN_SPEED_SNAPSHOT = False  # 标记不可用
    return _TOKEN_SPEED_SNAPSHOT if callable(_TOKEN_SPEED_SNAPSHOT) else None


class StatusRenderer:
    """状态行渲染器 — 状态格式化、工具计数、模型名管理。

    Attributes:
        model_name: 当前模型名称。
        tool_count: 当前运行中的工具调用数。
        tool_fail_count: 失败工具调用累计数。
        tool_total: 总工具调用累计数。
        status_active: 是否处于活跃刷新中（流式输出期间）。
    """

    def __init__(
        self,
        model_name: str = "",
        tool_count: int = 0,
        tool_fail_count: int = 0,
        tool_total: int = 0,
        status_active: bool = False,
    ):
        self.model_name = model_name
        self.tool_count = tool_count
        self.tool_fail_count = tool_fail_count
        self.tool_total = tool_total
        self.status_active = status_active
        self._last_status: str = ""

    # ── 生命周期 ──────────────────────────────────────────

    def enable_status(self) -> None:
        """激活状态行刷新（流式输出期间调用）。"""
        self.status_active = True
        self._last_status = ""

    def disable_status(self) -> None:
        """冻结状态行（流式结束后调用），仅显示模型名。

        将 status_active 置为 False，状态行从全量统计（耗时/令牌/速率）
        切换为仅显示模型名。调用方负责在之后触发重绘。
        """
        self.status_active = False

    # ── 工具计数 ──────────────────────────────────────────

    def increment_tool(self) -> None:
        """递增工具调用计数。"""
        self.tool_count += 1
        self.tool_total += 1

    def decrement_tool(self) -> None:
        """递减工具调用计数（工具成功完成时调用）。

        当 tool_done 事件 success=True 时，将运行中的工具计数减1，
        使用户在状态行看到工具计数动态减少的视觉反馈。
        """
        self.tool_count = max(0, self.tool_count - 1)

    def increment_tool_fail(self) -> None:
        """递增失败工具计数（工具完成且 success=False 时调用）。"""
        self.tool_fail_count += 1

    def reset_tool_count(self) -> None:
        """重置工具计数（新轮开始时清零）。"""
        self.tool_count = 0
        self.tool_fail_count = 0
        self.tool_total = 0

    # ── 模型名 ────────────────────────────────────────────

    def set_model_name(self, name: str) -> None:
        """设置当前模型名字，状态行实时更新。

        跨线程安全：由 monitor 线程（Ctrl+N/Ctrl+R 回调）和 asyncio 线程
        （_handle_round / 命令处理）两条路径写入。CPython GIL 保证
        简单 str 属性赋值原子安全。
        """
        self.model_name = name

    # ── 查询 ──────────────────────────────────────────────

    @property
    def is_status_active(self) -> bool:
        """状态行是否处于活跃刷新中（流式输出期间）。

        供 ChatUIConsumer 等外部调用方读取状态行刷新开关。
        """
        return self.status_active

    def get_status_elapsed(self) -> float:
        """获取状态行最后一次记录的耗时（秒），用于通知等场景。"""
        snap_func = _get_snapshot()
        if snap_func is None:
            return 0.0
        try:
            return snap_func().get("elapsed_seconds", 0.0)
        except Exception:
            return 0.0

    # ── 格式化 ────────────────────────────────────────────

    def format_status(
        self,
        model_name: Optional[str] = None,
        tool_count: Optional[int] = None,
        tool_fail_count: Optional[int] = None,
        tool_total: Optional[int] = None,
        status_active: Optional[bool] = None,
    ) -> str:
        """构建状态行文本（优雅信息风）。

        流式输出期间显示全量统计：模型名 · 耗时 · 令牌数 · 实时速率 · 工具计数。
        非流式空闲时仅显示模型名字（带 ◉ 图标），不显示任何统计信息。
        使用多色分层：模型名高亮（带 ◉）、耗时蓝灰色、令牌数灰色。
        工具计数值得高亮区分成功/失败（成功绿/失败红）。

        Args:
            model_name: 模型名，None 则使用实例默认值。
            tool_count: 运行中工具数，None 则使用实例默认值。
            tool_fail_count: 失败工具数，None 则使用实例默认值。
            tool_total: 总工具数，None 则使用实例默认值。
            status_active: 是否活跃刷新，None 则使用实例默认值。

        Returns:
            格式化后的状态行字符串（含 ANSI 颜色码）。
        """
        # ── 参数解析（显式参数优先，否则回退到实例属性） ──
        _model_name = model_name if model_name is not None else self.model_name
        _tool_count = tool_count if tool_count is not None else self.tool_count
        _tool_fail_count = tool_fail_count if tool_fail_count is not None else self.tool_fail_count
        _tool_total = tool_total if tool_total is not None else self.tool_total
        _status_active = status_active if status_active is not None else self.status_active

        # ── 模型名字（始终显示，带 ◉ 图标） ──
        model_part = (
            f"{_COLOR_ACCENT}\u25c9{_COLOR_RESET} {_COLOR_ACCENT}{_model_name}{_COLOR_RESET}"
            if _model_name else ""
        )

        # ★ 非流式活跃时仅显示模型名
        if not _status_active:
            return model_part

        snap_func = _get_snapshot()
        if snap_func is None:
            return model_part

        try:
            snap = snap_func()
        except Exception:
            return model_part

        total = snap.get("total_tokens", 0)           # 历史累计总tok
        elapsed = snap.get("elapsed_seconds", 0.0)    # 当轮耗时
        per_second_speed = snap.get("per_second_speed", 0.0)  # 实时 tok/s

        if total <= 0 and elapsed <= 0 and per_second_speed <= 0 and _tool_total <= 0:
            return model_part

        parts = []

        # 工具调用计数（带 ⚙ 图标，成功/失败分色）
        if _tool_total > 0:
            done = _tool_total - _tool_fail_count
            if _tool_fail_count > 0:
                parts.append(
                    f"{_COLOR_TOOL_OK}{done}{_COLOR_RESET}"
                    f"{_COLOR_DIM}/{_COLOR_RESET}"
                    f"{_COLOR_TOOL_FAIL}{_tool_total}{_COLOR_RESET}"
                )
            else:
                parts.append(f"{_COLOR_TOOL_OK}{_tool_total}{_COLOR_RESET}")

        # 耗时（蓝灰高亮）
        if elapsed > 0:
            if elapsed >= 60:
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                dur = f"{mins}:{secs:02d}" if mins < 60 else f"{mins // 60}:{mins % 60:02d}:{secs:02d}"
            else:
                dur = f"{elapsed:.1f}s"
            parts.append(f"{_COLOR_TIME}{dur}{_COLOR_RESET}")

        # 令牌数（靛蓝色，更醒目）
        if total > 0:
            tok_str = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
            parts.append(f"{_COLOR_TOKEN}{tok_str}t{_COLOR_RESET}")

        # 实时 token 速度（tok/s，琥珀色高亮）
        if per_second_speed > 0:
            if per_second_speed >= 1:
                speed_str = f"{per_second_speed:.1f}"
            else:
                speed_str = f"{per_second_speed:.2f}"
            parts.append(f"{_COLOR_SPEED}{speed_str}t/s{_COLOR_RESET}")

        sep = f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        status = sep.join(parts) if parts else ""
        if model_part and status:
            return f"{model_part}  {status}"
        return model_part or status
