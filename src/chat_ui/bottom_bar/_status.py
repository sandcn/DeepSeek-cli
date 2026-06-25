"""_BottomBar 状态管理 mixin — 状态行格式化、工具计数、token 速度快照。

从 _bottom_bar.py 提取，以 mixin 类形式嵌入 _BottomBar。

依赖 _BottomBar.__init__ 中初始化以下字段：
  - _status_active, _model_name, _tool_count, _tool_fail_count, _tool_total
"""

from __future__ import annotations

import logging
from typing import Optional

from ._theme import (
    _COLOR_DIM,
    _COLOR_MODEL_NAME,
    _COLOR_RESET,
    _COLOR_SPEED,
    _COLOR_STREAMING,
    _COLOR_TIME,
    _COLOR_TOKEN,
    _COLOR_TOOL_FAIL,
    _COLOR_TOOL_OK,
)

_logger = logging.getLogger(__name__)


# ── 模块级 get_token_speed_snapshot 缓存（避免内部方法每次 import） ──
_TOKEN_SPEED_SNAPSHOT: Optional[callable] = None  # 也可赋值为 False（标记不可用）


def _get_snapshot():
    """获取 get_token_speed_snapshot 函数引用（惰性加载，异常静默）。

    先检查 src.ui._bottom_bar_status（旧 stub 模块）是否被测试 patch 过；
    若 stub 中的 _get_snapshot 与当前函数不同（被 mock 替换），则使用 stub 版本。
    确保迁移期间旧测试路径的 patch 能生效。
    """
    global _TOKEN_SPEED_SNAPSHOT
    # ── 兼容测试 patch：检查旧路径是否被替换 ──
    try:
        import sys
        _stub_mod = sys.modules.get("src.ui._bottom_bar_status")
        if _stub_mod is not None:
            _stub_snap = _stub_mod._get_snapshot
            if _stub_snap is not _get_snapshot and callable(_stub_snap):
                # 测试已通过旧路径 patch，使用 stub 版本
                result = _stub_snap()
                if callable(result):
                    return result  # 返回 callable（如 lambda: dict）
                return result if result else None
    except Exception:
        pass
    # ── 正常路径：惰性加载 api.stats ──
    if _TOKEN_SPEED_SNAPSHOT is None:
        try:
            from ...api.stats import get_token_speed_snapshot
            _TOKEN_SPEED_SNAPSHOT = get_token_speed_snapshot
        except ImportError:
            _TOKEN_SPEED_SNAPSHOT = False  # 标记不可用
    return _TOKEN_SPEED_SNAPSHOT if callable(_TOKEN_SPEED_SNAPSHOT) else None


class _StatusMixin:
    """状态行管理 mixin — 状态格式化、工具计数、模型名管理。

    需嵌入 _BottomBar 类，要求宿主在 __init__ 中初始化：
      - self._status_active: bool = False
      - self._model_name: str = ""
      - self._tool_count: int = 0
      - self._tool_fail_count: int = 0
      - self._tool_total: int = 0
    """

    def enable_status(self) -> None:
        """激活状态行刷新（流式输出期间调用）。"""
        self._status_active = True
        self._last_status = ""  # 强制下次刷新

    def disable_status(self) -> None:
        """冻结状态行（流式结束后调用），仅显示模型名。

        将 _status_active 置为 False，状态行从全量统计（耗时/令牌/速率）
        切换为仅显示模型名。调用方负责在之后调用 request_bottom_redraw()，
        由 render 线程 _phase_redraw_bottom() 在下一周期触发 force_redraw()。
        """
        self._status_active = False

    @property
    def is_status_active(self) -> bool:
        """状态行是否处于活跃刷新中（流式输出期间）。

        供 ChatUIConsumer 等外部调用方读取状态行刷新开关，
        避免直接访问私有属性 _status_active。
        """
        return self._status_active

    def get_status_elapsed(self) -> float:
        """获取状态行最后一次记录的耗时（秒），用于通知等场景。"""
        snap_func = _get_snapshot()
        if snap_func is None:
            return 0.0
        try:
            return snap_func().get("elapsed_seconds", 0.0)
        except Exception:
            return 0.0

    def increment_tool(self) -> None:
        """递增工具调用计数。"""
        self._tool_count += 1
        self._tool_total += 1

    def decrement_tool(self) -> None:
        """递减工具调用计数（工具成功完成时调用）。

        当 tool_done 事件 success=True 时，将运行中的工具计数减1，
        使用户在状态行看到工具计数动态减少的视觉反馈。
        """
        self._tool_count = max(0, self._tool_count - 1)

    def increment_tool_fail(self) -> None:
        """递增失败工具计数（工具完成且 success=False 时调用）。"""
        self._tool_fail_count += 1

    def reset_tool_count(self) -> None:
        """重置工具计数（新轮开始时清零）。"""
        self._tool_count = 0
        self._tool_fail_count = 0
        self._tool_total = 0

    def set_model_name(self, name: str) -> None:
        """设置当前模型名字，状态行实时更新。

        跨线程安全：由 monitor 线程（Ctrl+N/Ctrl+R 回调）和 asyncio 线程
        （_handle_round / 命令处理）两条路径写入。CPython GIL 保证
        简单 str 属性赋值原子安全。读取方 _format_status() 始终在
        output_lock 保护下调用，不存在 torn read。
        """
        self._model_name = name

    def _format_status(self) -> str:
        """构建状态行文本（优雅信息风）。

        流式输出期间显示全量统计：模型名 · 耗时 · 令牌数 · 实时速率 · 工具计数。
        非流式空闲时仅显示模型名字（带 ⏣ 图标），不显示任何统计信息。
        使用多色分层：模型名高亮（带 ⏣）、耗时蓝灰色、令牌数灰色。
        工具计数值得高亮区分成功/失败（成功绿/失败红）。

        Claude Code 风格下（CHAT_UI_CLAUDE_STYLE=1）：整行使用 dim 样式，
        格式为 model_name · N tokens · $X.XX，去除颜色装饰。
        """
        # ── Claude Code 风格门控 ──
        try:
            from ..infrastructure.claude_style import (
                _is_claude_style_enabled,
                CLAUDE_COLORS,
            )
        except ImportError:
            _is_claude_style_enabled = lambda: False  # noqa: E731
            CLAUDE_COLORS = {}

        if _is_claude_style_enabled():
            muted = CLAUDE_COLORS.get("muted", _COLOR_DIM)

            # 非流式时仅显示模型名（dim 样式）
            if not self._status_active:
                return f"{muted}{self._model_name}{_COLOR_RESET}" if self._model_name else ""

            snap_func = _get_snapshot()
            if snap_func is None:
                return f"{muted}{self._model_name}{_COLOR_RESET}" if self._model_name else ""

            try:
                snap = snap_func()
            except Exception:
                return f"{muted}{self._model_name}{_COLOR_RESET}" if self._model_name else ""

            total = snap.get("total_tokens", 0)

            parts = []
            if self._model_name:
                parts.append(self._model_name)
            if total > 0:
                tok_str = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
                parts.append(f"{tok_str} tokens")
            else:
                parts.append("0 tokens")

            # 尝试计算费用
            try:
                from ...api._stats_core import get_token_stats
                stats = get_token_stats()
                cost = stats["input"] / 1000 * 0.01 + stats["output"] / 1000 * 0.03
                parts.append(f"${cost:.2f}")
            except Exception:
                parts.append("$0.00")

            sep = f" {muted}\u00b7 "
            return f"{muted}{sep.join(parts)}{_COLOR_RESET}"

        # ── 模型名字（始终显示，带 ⏣ 图标） ──
        model_part = (
            f"{_COLOR_MODEL_NAME}\u23e3{_COLOR_RESET} {_COLOR_MODEL_NAME}{self._model_name}{_COLOR_RESET}"
            if self._model_name else ""
        )

        # ★ 非流式活跃时仅显示模型名
        if not self._status_active:
            return model_part

        # 流式输出脉冲指示器
        streaming_indicator = f"{_COLOR_STREAMING}●{_COLOR_RESET} "

        snap_func = _get_snapshot()
        if snap_func is None:
            return f"{streaming_indicator}{model_part}"

        try:
            snap = snap_func()
        except Exception:
            return f"{streaming_indicator}{model_part}"

        total = snap.get("total_tokens", 0)           # 历史累计总tok
        elapsed = snap.get("elapsed_seconds", 0.0)    # 当轮耗时
        per_second_speed = snap.get("per_second_speed", 0.0)  # 实时 tok/s

        if total <= 0 and elapsed <= 0 and per_second_speed <= 0 and self._tool_total <= 0:
            return f"{streaming_indicator}{model_part}"

        parts = []

        # 工具调用计数（带 ⚙ 图标，成功/失败分色）
        if self._tool_total > 0:
            done = self._tool_total - self._tool_fail_count
            if self._tool_fail_count > 0:
                parts.append(
                    f"⚙ {_COLOR_TOOL_OK}{done}{_COLOR_RESET}/{_COLOR_TOOL_FAIL}{self._tool_total}{_COLOR_RESET}"
                )
            else:
                parts.append(f"⚙ {_COLOR_TOOL_OK}{self._tool_total}{_COLOR_RESET}")

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
            return f"{streaming_indicator}{model_part}  {status}"
        return f"{streaming_indicator}{model_part}" if model_part else streaming_indicator.rstrip()
