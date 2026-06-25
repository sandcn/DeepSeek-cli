"""终端光标视觉位置计算 — 拆行/制表符展开/ANSI视觉宽度。

从 _bottom_bar.py 提取的纯计算函数，无状态无锁，纯函数式设计。
供 _BottomBar 和 RenderEngine.position_cursor 使用。

职责范围：
  - 制表符展开（_expand_tabs / _tab_pos_to_expanded）
  - 按终端列宽拆行（_wrap_by_width）
  - 光标在拆行文本中的视觉定位（_compute_cursor_visual_pos）
  - ANSI 转义码感知的视觉宽度计算（_visual_len）
  - 终端列宽截断（_truncate_by_width）
"""

from __future__ import annotations

import sys
from typing import Callable

from wcwidth import wcswidth


__all__ = [
    "_TAB_WIDTH",
    "_truncate_by_width",
    "_expand_tabs",
    "_tab_pos_to_expanded",
    "_wrap_by_width",
    "_compute_cursor_visual_pos",
    "_visual_len",
    "CursorController",
]


_TAB_WIDTH = 4  # 制表符宽度（列数）


def _truncate_by_width(s: str, max_width: int) -> str:
    """按终端列宽截断字符串（中文占 2 列）。"""
    w = 0
    for i, ch in enumerate(s):
        cw = wcswidth(ch) if wcswidth(ch) >= 0 else 1
        if w + cw > max_width:
            return s[:i]
        w += cw
    return s


def _expand_tabs(text: str, start_col: int = 0, tab_width: int | None = None) -> str:
    """将制表符按制表位展开为空格。

    每个 \\t 跳到下一个制表位列（tab_width 的整数倍），
    用空格填充至该列。

    Args:
        text: 含制表符的文本。
        start_col: 起始列（0-based）。
        tab_width: 制表宽度，默认 _TAB_WIDTH。

    Returns:
        展开后的纯空格文本。
    """
    if tab_width is None:
        tab_width = _TAB_WIDTH
    if '\t' not in text:
        return text
    result = []
    col = start_col
    for ch in text:
        if ch == '\n':
            result.append(ch)
            col = 0  # 换行后列计数器归零
        elif ch == '\t':
            spaces = tab_width - (col % tab_width)
            result.append(' ' * spaces)
            col += spaces
        else:
            cw = wcswidth(ch)
            result.append(ch)
            col += cw if cw >= 0 else 1
    return ''.join(result)


def _tab_pos_to_expanded(text: str, pos: int,
                         tab_width: int | None = None) -> int:
    """将含制表符文本中的字符位置映射到展开后的位置。

    Args:
        text: 含制表符的原始文本。
        pos: 原始文本中的字符索引（<0 返回 -1）。
        tab_width: 制表宽度，默认 _TAB_WIDTH。

    Returns:
        展开后文本中对应的字符索引。
    """
    if pos < 0:
        return -1
    if tab_width is None:
        tab_width = _TAB_WIDTH
    expanded_pos = 0
    col = 0
    for i, ch in enumerate(text):
        if i >= pos:
            break
        if ch == '\t':
            spaces = tab_width - (col % tab_width)
            expanded_pos += spaces
            col += spaces
        elif ch == '\n':
            expanded_pos += 1
            col = 0  # 换行后列计数器归零
        else:
            cw = wcswidth(ch)
            expanded_pos += 1
            col += cw if cw >= 0 else 1
    return expanded_pos


def _wrap_by_width(s: str, max_width: int) -> list[str]:
    """按终端列宽拆分文本为多行，每行不超过 max_width 列。

    优先按 \\n 拆分（强制换行），再对每段按列宽拆行。
    调用方应先通过 _expand_tabs 展开制表符。
    """
    if max_width <= 0 or not s:
        return [s] if s else [""]
    lines: list[str] = []
    # 先按 \n 拆分为强制换行段
    for segment in s.split('\n'):
        remaining = segment
        while remaining:
            w = 0
            idx = 0
            for i, ch in enumerate(remaining):
                cw = wcswidth(ch) if wcswidth(ch) >= 0 else 1
                if w + cw > max_width:
                    break
                w += cw
                idx = i + 1
            if idx == 0:  # 单个字符超过宽度，至少取一个字符
                idx = 1
            lines.append(remaining[:idx])
            remaining = remaining[idx:]
        if not segment:
            # 空段表示连续 \n 或尾部 \n → 插入一个空行
            lines.append("")
    return lines if lines else [""]


def _compute_cursor_visual_pos(
    text: str, cursor_pos: int, max_width: int,
) -> tuple[int, int]:
    """计算光标在带 \\n 的文本中的视觉位置（行号, 列号）。

    将文本按 \\n 拆分为逻辑行，每行分别制表符展开和按列宽拆行，
    定位光标所在逻辑行，累计前面逻辑行的视觉行数得到总行号偏移。

    Args:
        text: 原始输入文本（含 \\n）。
        cursor_pos: 光标在原始文本中的字符偏移（-1=末尾）。
        max_width: 每行最大列宽。

    Returns:
        (visual_line_idx, visual_col) —— 均为 0-based。
    """
    if not text:
        return (0, 0)

    # 确定绝对光标位置
    if cursor_pos < 0:
        abs_cursor = len(text)
    else:
        abs_cursor = cursor_pos

    # 拆分为逻辑行
    lines = text.split('\n')
    cum = 0  # 累计原始字符索引
    for logical_idx, logical_line in enumerate(lines):
        line_len = len(logical_line)
        if abs_cursor <= cum + line_len:
            # 光标在此逻辑行中（或在行末的 \n 上）
            pos_in_line = abs_cursor - cum

            # 展开并拆行
            expanded = _expand_tabs(logical_line)
            wrapped = _wrap_by_width(expanded, max_width)

            # 计算此逻辑行内光标所处视觉行和列
            expanded_in_line = _tab_pos_to_expanded(logical_line, pos_in_line)
            if expanded_in_line < 0:
                # 末尾
                last_seg = wrapped[-1] if wrapped else ""
                col_in_line = wcswidth(last_seg)
                visual_line_in_logical = len(wrapped) - 1 if wrapped else 0
            else:
                cum2 = 0
                visual_line_in_logical = 0
                for i, seg in enumerate(wrapped):
                    if expanded_in_line <= cum2 + len(seg):
                        visual_line_in_logical = i
                        prefix = seg[:expanded_in_line - cum2]
                        col_in_line = wcswidth(prefix)
                        break
                    cum2 += len(seg)
                else:
                    visual_line_in_logical = len(wrapped) - 1 if wrapped else 0
                    col_in_line = wcswidth(wrapped[-1]) if wrapped else 0

            # 累计前面逻辑行的视觉行数
            total_before = 0
            for prev_line in lines[:logical_idx]:
                prev_expanded = _expand_tabs(prev_line)
                total_before += len(_wrap_by_width(prev_expanded, max_width))

            return (total_before + visual_line_in_logical, col_in_line)

        # 此逻辑行已消耗：字符数 + \n 的 1 个字符
        cum += line_len + 1

    # 超出范围 → 末尾
    # 最后一个逻辑行末尾
    last_line = lines[-1] if lines else ""
    expanded = _expand_tabs(last_line)
    wrapped = _wrap_by_width(expanded, max_width)
    last_seg = wrapped[-1] if wrapped else ""
    col = wcswidth(last_seg)
    total_before = 0
    for prev_line in lines[:-1]:
        prev_expanded = _expand_tabs(prev_line)
        total_before += len(_wrap_by_width(prev_expanded, max_width))
    visual_row = total_before + (len(wrapped) - 1 if wrapped else 0)
    return (visual_row, col)


def _visual_len(s: str) -> int:
    """计算不含 ANSI 转义序列的视觉宽度。

    识别所有 CSI 序列（\\033[...终止字母），正确跳过；
    同时也处理 OSC/APC 等其他 ANSI 序列类型。
    已知限制：不处理多码点组合字符（grapheme cluster）。
    """
    w = 0
    i = 0
    while i < len(s):
        if s[i] == '\033':
            j = i + 1
            if j < len(s) and s[j] == '[':
                # CSI 序列: \033[...终止字母(A-Za-z)
                j += 1
                while j < len(s) and s[j] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                    j += 1
                i = j + 1 if j < len(s) else len(s)
            elif j < len(s) and s[j] in ']PX^_':
                # OSC/APC/DCS/PM/SOS: \033]...\a 或 \033]...\033\\
                j += 1
                while j < len(s):
                    if s[j] == '\033':
                        if j + 1 < len(s) and s[j + 1] == '\\':
                            i = j + 2
                            break
                    elif s[j] == '\a':
                        i = j + 1
                        break
                    j += 1
                else:
                    i = len(s)
            else:
                # 非 CSI 控制序列（如 \033[无参数]），跳过
                i = j + 1
        else:
            cw = wcswidth(s[i])
            w += cw if cw >= 0 else 1
            i += 1
    return w


# ═══════════════════════════════════════════════════════════
# CursorController — 光标定位控制器
# ═══════════════════════════════════════════════════════════

class CursorController:
    """光标定位控制器 — 封装 blessed/ANSI 光标操作。

    负责：光标定位(position_cursor)、移动到底部(move_cursor_to_bottom)、
    确保光标在上部(ensure_cursor_upper)、写 ANSI 转义序列(_write_ansi)。

    构造注入：get_terminal 回调（可选），用于测试/DI；None 时使用
    src.ui._blessed.get_terminal 模块级默认值。
    """

    def __init__(self, bottom_bar, get_terminal: Callable | None = None):
        self._bb = bottom_bar
        if get_terminal is not None:
            self._get_terminal = get_terminal
        else:
            self._get_terminal = self._default_get_terminal

    @staticmethod
    def _default_get_terminal():
        """模块级默认值：延迟导入避免循环依赖。"""
        from ..infrastructure.terminal import get_terminal
        return get_terminal()

    def _write_ansi(self, text: str, fallback: str) -> None:
        """写 ANSI 转义序列：优先 blessed 路径，失败回退 raw ANSI。

        Args:
            text: blessed 生成的 ANSI 转义序列。
            fallback: 当 blessed 不可用或 write 失败时的回退序列。
        """
        try:
            self._get_terminal()
            sys.__stdout__.write(text)
        except Exception:
            sys.__stdout__.write(fallback)
        sys.__stdout__.flush()

    def position_cursor(self) -> None:
        """根据 _bb 的光标信息定位终端光标。

        委托 _bb.get_cursor_info() + _bb.compute_cursor_position()
        获取目标行列，通过 blessed（优先）或 raw ANSI（回退）写入。
        """
        text, cursor_pos, h, w = self._bb.get_cursor_info()
        r_cursor, cursor_col = self._bb.compute_cursor_position(text, cursor_pos, h, w)
        try:
            term = self._get_terminal()
            sys.__stdout__.write(term.move_xy(cursor_col - 1, r_cursor - 1))
        except Exception:
            sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
        sys.__stdout__.flush()

    def ensure_cursor_upper(self) -> None:
        """委托 _bb 确保光标在上部区域。"""
        self._bb.ensure_cursor_in_upper()

    def move_cursor_to_bottom(self) -> None:
        """移动光标到终端底部。

        优先通过 blessed term.move_xy(0, height-1)，失败回退
        _ANSI_CURSOR_BOTTOM 常量。
        """
        from ..commands.const import _ANSI_CURSOR_BOTTOM
        try:
            term = self._get_terminal()
            ans_seq = term.move_xy(0, term.height - 1)
            self._write_ansi(ans_seq, _ANSI_CURSOR_BOTTOM)
        except Exception:
            sys.__stdout__.write(_ANSI_CURSOR_BOTTOM)
            sys.__stdout__.flush()
