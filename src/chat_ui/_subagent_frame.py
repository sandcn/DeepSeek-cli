"""SubAgent 面板帧渲染器 — 从 _renderer.py 提取的独立 ANSI 滚动逻辑。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter

_logger = logging.getLogger(__name__)


class SubagentFrameRenderer:
    """SubAgent 面板帧渲染器。

    负责将并行 SubAgent 的面板帧通过 ANSI 转义序列渲染到终端。
    支持两种模式：
    - scroll 模式（scroll_end > 0）：利用终端滚动区域
    - 回退模式（scroll_end == 0）：Blessed 移动光标 + 逐行覆写
    """

    def render(self, frame_lines: tuple, adapter: "OutputAdapter") -> None:
        """渲染 SubAgent 面板帧。

        Args:
            frame_lines: (lines, scroll_end, last_lines, clear_eol) 元组
            adapter: OutputAdapter 实例
        """
        if not frame_lines:
            return
        if len(frame_lines) < 4:
            return
        lines = frame_lines[0]
        scroll_end = frame_lines[1]
        last_lines = frame_lines[2]
        clear_eol = frame_lines[3]
        if not lines or not isinstance(lines, (list, tuple)):
            return
        total = len(lines)
        buf = ""
        if scroll_end > 0 and total > scroll_end:
            lines = lines[total - scroll_end:]
            total = scroll_end
        if scroll_end > 0 and last_lines > 0 and total > last_lines:
            delta = total - last_lines
            buf += f"\033[{scroll_end};1H\033[{delta}S"
        if scroll_end > 0:
            start_row = scroll_end - total + 1
            clear_start = start_row
            if last_lines > 0:
                old_start = scroll_end - last_lines + 1
                if old_start < clear_start:
                    clear_start = old_start
            if clear_start < 1:
                clear_start = 1
            for r in range(clear_start, scroll_end + 1):
                buf += f"\033[{r};1H{clear_eol}"
            buf += f"\033[{start_row};1H"
            for i, line in enumerate(lines):
                buf += line
                if i < total - 1:
                    buf += "\n"
            restore_delta = 0
            if last_lines > 0 and total < last_lines:
                restore_delta = last_lines - total
            if restore_delta > 0:
                buf += f"\033[{scroll_end};1H\033[{restore_delta}T"
                for r in range(1, restore_delta + 1):
                    buf += f"\033[{r};1H{clear_eol}"
            adapter.write_raw_buffered(buf)
            return
        try:
            from ._blessed import get_terminal
            term = get_terminal()
            move_up = term.move_up
            sc = term.sc if term.sc else "\033[s"
            rc = term.rc if term.rc else "\033[u"
        except Exception:
            _logger.debug("subagent_frame Blessed 不可用, 使用 ANSI 回退", exc_info=True)
            move_up = lambda n: f"\033[{n}A"
            sc = "\033[s"
            rc = "\033[u"
        buf = ""
        if last_lines > 0:
            buf += rc
            buf += move_up(last_lines)
        for i, line in enumerate(lines):
            buf += "\r" + clear_eol + line
            if i < total - 1:
                buf += "\n"
        extra = last_lines - total
        if extra > 0:
            buf += "\n" + sc
            for _ in range(extra):
                buf += "\n" + clear_eol
        else:
            buf += "\n" + sc
        adapter.write_raw_buffered(buf)
