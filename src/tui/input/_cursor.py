"""CursorPositioner — 纯计算光标定位，零 I/O。

从 _BottomBar 的 ensure_cursor_in_lower() 和 compute_cursor_position()
中提取的光标定位计算逻辑。职责：
  - 根据输入文本、光标位置和底部栏布局参数，计算光标在终端上的行号和列号
  - 不执行任何终端 I/O（不写 sys.__stdout__），仅返回计算结果
  - 通过依赖注入获取 TerminalWidthCache 和 CursorTracker

设计原则：
  - 纯函数式核心 + 依赖注入边界
  - 与 _BottomBar 的关键差异：不缓存拆行结果（由调用方管理）
  - 复用 cursor.py 中的纯函数（_compute_cursor_visual_pos 等）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...terminal.terminal import TerminalWidthCache

from ..widgets.bottom_bar.cursor import _compute_cursor_visual_pos


class CursorPositioner:
    """光标定位计算器 — 纯计算，零 I/O。

    计算光标在终端上的行号（1-based）和列号（1-based），
    以及对应的视觉位置（0-based 行/列）。

    构造函数通过依赖注入获取 TerminalWidthCache 和 CursorTracker，
    确保所有外部依赖可 mock，便于单元测试。
    """

    def __init__(
        self,
        width_cache: "TerminalWidthCache",
        cursor_tracker=None,
    ) -> None:
        """初始化 CursorPositioner。

        Args:
            width_cache: TerminalWidthCache 实例（通过 DI 注入）。
            cursor_tracker: 可选，全局光标追踪器（保留给后续集成使用）。
        """
        self._width_cache = width_cache
        self._cursor_tracker = cursor_tracker

    def compute(
        self,
        text: str,
        cursor_pos: int,
        bottom_lines: int,
        subagent_lines: int,
        completion_height: int,
    ) -> tuple[int, int, int, int]:
        """计算光标在终端上的位置。

        根据输入文本、光标偏移和底部栏布局参数，
        计算光标应当在终端的行号和列号。

        Args:
            text: 输入文本（含 \\n）。
            cursor_pos: 光标在文本中的偏移位置（-1=末尾）。
            bottom_lines: 底部栏总行数（含分隔线、状态行、输入行、补全弹窗）。
            subagent_lines: subagent 面板行数。
            completion_height: 补全弹窗高度（行数）。

        Returns:
            (r_cursor, cursor_col, vis_row, vis_col) 四元组：
              - r_cursor: 终端行号（1-based）
              - cursor_col: 终端列号（1-based）
              - vis_row: 视觉行（0-based）
              - vis_col: 视觉列（0-based）
        """
        width = self._width_cache.get_width()
        height = self._width_cache.get_height()

        max_input = max(1, width - 4)
        vis_row, vis_col = _compute_cursor_visual_pos(
            text, cursor_pos, max_input,
        )

        # 计算终端行号（与原 _BottomBar.compute_cursor_position / ensure_cursor_in_lower 一致）
        # +4 跳过 分隔线(1) + 子Agent面板行(1) + 状态行(1) + 上分割线(1)
        # +len(subagent_lines) 补偿分隔线与状态行之间的 subagent 面板行
        r_cursor = (
            height - bottom_lines + 4 + subagent_lines + completion_height + vis_row
        )
        r_cursor = max(1, min(r_cursor, height))

        cursor_col = min(3 + vis_col, width)

        return (r_cursor, cursor_col, vis_row, vis_col)
